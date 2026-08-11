export const meta = {
  name: 'workstream-honesty',
  description: 'Audit every work-item state in plan/07-workstreams.md against build evidence, then reconcile the Jira board (read-only) against what the plan now claims',
  whenToUse: 'After a build push or before a demo: catch workstream items whose recorded state has drifted from the code/deploy/git reality, and list the Jira orphans a re-sync would leave — without editing the plan or touching the board',
  phases: [
    { title: 'Discover', detail: "run jira_sync's own parser to get the states the plan claims today" },
    { title: 'Audit', detail: 'one agent per workstream, each item state vs code / deploy / git / results' },
    { title: 'Verify', detail: 'adversarial refutation of each claimed drift — keep the claim if uncertain' },
    { title: 'Reconcile', detail: 'expected board (parser) vs live board — enumerate stale/missing, delete nothing' },
  ],
}

// ── schemas ──────────────────────────────────────────────────────────────

// Discover returns exactly what jira_sync.parse_plan() emits — the states the
// board would show after a --apply — so the audit compares against the plan's
// OWN computed claims, never a re-reading of the prose that could drift from it.
const WORKSTREAMS = {
  type: 'object',
  required: ['workstreams'],
  properties: {
    workstreams: {
      type: 'array',
      items: {
        type: 'object',
        required: ['ws', 'title', 'done', 'items'],
        properties: {
          ws: { type: 'string', description: 'e.g. "WS8"' },
          title: { type: 'string' },
          done: { type: 'boolean', description: "the epic's computed done flag (all items done)" },
          status: { type: 'string', description: 'the verbatim status paragraph' },
          items: {
            type: 'array',
            items: {
              type: 'object',
              required: ['n', 'summary', 'state', 'done'],
              properties: {
                n: { type: 'integer' },
                summary: { type: 'string' },
                state: { type: 'string', description: 'the recorded state text' },
                done: { type: 'boolean', description: 'the state as jira_sync computes it' },
              },
            },
          },
        },
      },
    },
  },
}

const WS_FINDING = {
  type: 'object',
  required: ['ws', 'epic_status_ok', 'items'],
  properties: {
    ws: { type: 'string' },
    // Does the verbatim status paragraph still describe reality? (e.g. "in
    // flight" on a workstream whose every item shipped.)
    epic_status_ok: { type: 'boolean' },
    epic_note: { type: 'string', description: 'why the status prose is or is not honest now' },
    items: {
      type: 'array',
      items: {
        type: 'object',
        required: ['n', 'summary', 'claimed_done', 'proposed_done', 'drift'],
        properties: {
          n: { type: 'integer' },
          summary: { type: 'string' },
          claimed_done: { type: 'boolean', description: 'the state the plan records today' },
          proposed_done: { type: 'boolean', description: 'the state the EVIDENCE supports' },
          // drift is either direction: an overclaim (claimed done, is not) OR an
          // underclaim (claimed not-done, evidence proves it shipped).
          drift: { type: 'boolean' },
          note: { type: 'string', description: 'one line: what the evidence shows' },
          evidence: {
            type: 'array',
            items: { type: 'string' },
            description: 'file:line, deploy script, commit sha, or plan/03 anchor',
          },
        },
      },
    },
  },
}

const VERDICT = {
  type: 'object',
  required: ['upheld', 'reason'],
  properties: {
    // upheld = the DRIFT is real: the plan's recorded state really is wrong and
    // a reader would be misled. Default false → keep the plan's claim, the
    // conservative direction (do not churn the plan on a maybe).
    upheld: { type: 'boolean' },
    reason: { type: 'string' },
  },
}

const RECONCILE = {
  type: 'object',
  properties: {
    skipped: { type: 'string', description: 'set iff JIRA_* creds are absent' },
    expected: { type: 'integer', description: 'issues the plan would produce (epics + stories)' },
    board: { type: 'integer', description: 'issues currently on the board' },
    stale: {
      type: 'array',
      description: 'board issues whose summary is NOT in the expected set — orphans to review',
      items: {
        type: 'object',
        required: ['key', 'summary'],
        properties: { key: { type: 'string' }, summary: { type: 'string' } },
      },
    },
    missing: {
      type: 'array',
      items: { type: 'string' },
      description: 'expected summaries not on the board — must be 0 before any prune',
    },
    duplicate_summaries: {
      type: 'array',
      items: {
        type: 'object',
        properties: { summary: { type: 'string' }, keys: { type: 'array', items: { type: 'string' } } },
      },
    },
  },
}

// ── Discover ─────────────────────────────────────────────────────────────
// Deterministic: the agent just RUNS the parser the board is generated from, so
// "what the plan claims" is exactly what a --apply would push — not a re-parse
// that could disagree with jira_sync.
phase('Discover')
const found = await agent(
  'In this repo run, verbatim:\n' +
    "  uv run python -c \"import sys; sys.path.insert(0,'scripts'); import json, jira_sync; " +
    'print(json.dumps(jira_sync.parse_plan()))"\n' +
    'That prints a JSON array of workstreams, each {ws, title, adrs, done, status, ' +
    'items:[{n, summary, state, done}]}. Return {workstreams: <that array>} unchanged — ' +
    'keep every workstream and every item, do not sample, summarise, or re-judge any state. ' +
    'You are a runner here, not an auditor.',
  { schema: WORKSTREAMS, agentType: 'general-purpose', effort: 'low' },
)
log(`${found.workstreams.length} workstreams, ${found.workstreams.reduce((n, w) => n + w.items.length, 0)} work items to audit`)

// ── Audit → Verify ─────────────────────────────────────────────────────────
// One agent per workstream (matches insights-audit's per-insight fan-out): each
// item's evidence lives in a different place, so a per-workstream reader that
// gathers that evidence once is the honest unit. Verify runs per workstream only
// when it carries a drift, so a clean workstream costs one agent, not two.
const results = await pipeline(
  found.workstreams,
  w =>
    agent(
      `Audit workstream ${w.ws} of the A2A interop lab for DELIVERY honesty: ${JSON.stringify(w)}.\n` +
        'For each item, claimed_done is the state the plan records today. Decide proposed_done — ' +
        'the state the EVIDENCE supports — then set drift = (proposed_done !== claimed_done).\n' +
        'Evidence, in this order of authority:\n' +
        '(1) CODE/CONFIG — does the module, adapter, client, config entry or scenario the item ' +
        'names actually exist under src/ or config/ and do what the item claims?\n' +
        '(2) DEPLOY — an item claiming a HOSTED thing (a Fargate face, an AgentCore runtime, a ' +
        'Lambda, Agent Engine, Foundry, a federation) needs the deploy/ script or handler that ' +
        'creates it. Hosted-claim backed only by local code is NOT done.\n' +
        '(3) GIT — `git log --oneline` / `git show <sha>`: is there a commit that landed this? ' +
        'A commit sha or trace id in the state cell should resolve.\n' +
        '(4) MEASURED RUNS — an item claiming a number or a "measured" pass needs a recorded run ' +
        'in plan/03-results.md, a dated ADR in plan/00-decisions.md, or a measured subsection of ' +
        'plan/07 itself (later workstreams record numbers in the workstream doc — legitimate).\n' +
        'RULES, matching how this plan is kept honest:\n' +
        '• Be CONSERVATIVE. Only propose done=true when the evidence PROVES it shipped; only ' +
        'propose done=false when the evidence shows it is incomplete, blocked, or an operator ' +
        'action not yet taken. If the evidence is ambiguous, set proposed_done = claimed_done ' +
        '(no drift) — the plan gets the benefit of the doubt, we do not churn it on a maybe.\n' +
        '• "operator action", "blocked", "not started/done" are legitimately NOT done even if ' +
        'the surrounding code exists — an un-taken human step is real incompleteness.\n' +
        '• Also judge epic_status_ok: does the verbatim status paragraph still describe reality ' +
        '(e.g. "(in flight)" on a workstream whose every item shipped is dishonest)? Give the ' +
        'one-line fix in epic_note.\n' +
        'Cite file:line, a deploy script path, a commit sha, or a plan/03 anchor in evidence for ' +
        'every drift. A drift with no evidence is not a drift.',
      { phase: 'Audit', label: `audit:${w.ws}`, schema: WS_FINDING, agentType: 'general-purpose' },
    ),
  (f, w) => {
    if (!f) return f
    const drifted = (f.items || []).filter(it => it.drift)
    if (!drifted.length && f.epic_status_ok) return f // clean — no verify agent
    return agent(
      `Adversarially verify the claimed delivery drifts in workstream ${f.ws} — try to REFUTE ` +
        `each one, i.e. argue the plan's ORIGINAL recorded state was right after all.\n` +
        `Claimed drifts: ${JSON.stringify(drifted)}\n` +
        (f.epic_status_ok ? '' : `Claimed status-prose problem: ${f.epic_note}\n`) +
        'Re-gather the evidence YOURSELF — reread the cited files, re-run the cited git/deploy ' +
        'checks. A drift is upheld ONLY if the plan\'s state really is wrong and a reader would ' +
        'be misled; default upheld=false if uncertain (keep the plan as written). Judge the set ' +
        'as a whole: upheld=true iff at least one drift or the status-prose problem is real and ' +
        'material.',
      { phase: 'Verify', label: `verify:${f.ws}`, schema: VERDICT, agentType: 'general-purpose' },
    ).then(v => ({ ...f, upheld: v.upheld, verify_reason: v.reason }))
  },
)

const flat = results.filter(Boolean)
// A confirmed edit list: only drifts in workstreams whose verify pass upheld them.
// (A clean workstream never got a verify agent, so `upheld` is undefined there —
// and it has no drifts anyway, so it contributes nothing.)
const confirmed_drifts = flat
  .filter(f => f.upheld)
  .flatMap(f =>
    (f.items || [])
      .filter(it => it.drift)
      .map(it => ({
        ws: f.ws,
        item: `${f.ws}.${it.n}`,
        summary: it.summary,
        from: it.claimed_done ? 'done' : 'not done',
        to: it.proposed_done ? 'done' : 'not done',
        note: it.note,
        evidence: it.evidence || [],
      })),
  )
const epic_status_issues = flat
  .filter(f => f.upheld && !f.epic_status_ok)
  .map(f => ({ ws: f.ws, fix: f.epic_note }))

// ── Reconcile (read-only) ────────────────────────────────────────────────
// The second half of the delivery-honesty story (plan/11): after the plan is
// made honest and re-synced, a reworded heading/item ORPHANS its old Jira issue
// (jira_sync matches by exact summary and never deletes). This stage computes
// the expected board from the parser and diffs it against the LIVE board to
// enumerate the orphans — read-only. It deletes nothing: pruning is an operator
// step over a reviewed, enumerated key list (plan/11), and the auto-mode
// classifier rightly refuses an open-ended delete sweep.
phase('Reconcile')
const reconcile = await agent(
  'In this repo run this exact python and return its JSON output as your structured result ' +
    '(it is READ-ONLY — GET/search against Jira, no writes):\n' +
    '  uv run python - <<\'PY\'\n' +
    '  import sys, os, json\n' +
    "  sys.path.insert(0, 'scripts')\n" +
    // load_dotenv() with no arg walks the call stack to find .env and asserts
    // out when python reads from stdin (a heredoc) — pass the path explicitly.
    "  from dotenv import load_dotenv; load_dotenv(os.path.join(os.getcwd(), '.env'))\n" +
    "  need = ('JIRA_SITE_URL', 'JIRA_EMAIL', 'JIRA_API_TOKEN')\n" +
    '  if not all(os.environ.get(v) for v in need):\n' +
    "      print(json.dumps({'skipped': 'JIRA_* not set in .env — reconcile needs board access'})); sys.exit()\n" +
    '  import jira_sync\n' +
    '  plan = jira_sync.parse_plan()\n' +
    '  expected = {}\n' +
    '  for w in plan:\n' +
    "      expected[f\"{w['ws']} — {w['title']}\"] = 'epic'\n" +
    "      for it in w['items']:\n" +
    "          expected[f\"{w['ws']}.{it['n']} — {it['summary']}\"[:255]] = 'story'\n" +
    '  index, dupes, token = {}, {}, None\n' +
    '  while True:\n' +
    "      body = {'jql': f'project = {jira_sync.PROJECT} ORDER BY key', 'maxResults': 100, 'fields': ['summary']}\n" +
    "      if token: body['nextPageToken'] = token\n" +
    "      res = jira_sync._api('POST', '/rest/api/3/search/jql', body)\n" +
    "      for issue in res.get('issues') or []:\n" +
    "          s, k = issue['fields']['summary'].strip(), issue['key']\n" +
    '          if s in index: dupes.setdefault(s, [index[s]]).append(k)\n' +
    '          else: index[s] = k\n' +
    "      token = res.get('nextPageToken')\n" +
    "      if not token or res.get('isLast'): break\n" +
    "  stale = [{'key': index[s], 'summary': s} for s in index if s not in expected]\n" +
    '  missing = [s for s in expected if s not in index]\n' +
    "  print(json.dumps({'expected': len(expected), 'board': len(index), 'stale': stale,\n" +
    "      'missing': missing,\n" +
    "      'duplicate_summaries': [{'summary': s, 'keys': ks} for s, ks in dupes.items()]}))\n" +
    '  PY\n' +
    'Return the parsed JSON object exactly. Do not delete or modify any issue.',
  { phase: 'Reconcile', label: 'reconcile:board', schema: RECONCILE, agentType: 'general-purpose', effort: 'low' },
)

const artifact =
  `Audited ${flat.length} workstreams · ${confirmed_drifts.length} confirmed item-state drift(s) · ` +
  `${epic_status_issues.length} stale status paragraph(s). ` +
  (reconcile?.skipped
    ? `Board reconcile skipped (${reconcile.skipped}).`
    : `Board: ${reconcile?.board ?? '?'} issues vs ${reconcile?.expected ?? '?'} expected — ` +
      `${(reconcile?.stale || []).length} stale, ${(reconcile?.missing || []).length} missing, ` +
      `${(reconcile?.duplicate_summaries || []).length} duplicate summaries.`)
log(artifact)

return {
  audited: flat.length,
  // The plan edits to make (each a done/not-done flip with evidence). Apply
  // these to plan/07, then re-run jira_sync (--apply) BEFORE reading `stale` as
  // a prune list — the stale set below reflects the board vs the plan AS IT IS
  // NOW, so it will shift once the drifts are corrected and re-synced.
  confirmed_drifts,
  epic_status_issues,
  reconcile, // read-only orphan report; deletion is an operator step (plan/11)
  artifact,
}
