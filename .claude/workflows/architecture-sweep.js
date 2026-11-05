export const meta = {
  name: 'architecture-sweep',
  description: 'Check every architecture diagram and console Details pane against how the lab is actually deployed',
  whenToUse: 'After a component moves host, changes identity, or stops being manual: verify no diagram or console copy still describes the old shape',
  phases: [
    { title: 'Inventory', detail: 'collect every architectural assertion — diagrams, Details panes, plan prose' },
    { title: 'Check', detail: 'one agent per assertion, against deploy scripts and live infrastructure' },
    { title: 'Verify', detail: 'adversarial refutation before anything is reported as wrong' },
    { title: 'Drawings', detail: 'redraw + rebuild + restamp every draw.io twin whose mermaid moved on (D113)' },
    { title: 'Log', detail: 'persist the run to traces/workflows/ (gitignored)' },
  ],
}

// WHY THIS IS SEPARATE FROM matrix-honesty-sweep.
//
// That sweep asks "does plan/02-matrix.md claim a cell the lab cannot back?" —
// its corpus is the matrix, its evidence is targets.yaml and recorded runs, and
// its failure mode is an OVERCLAIM about protocol support.
//
// This one asks "does any picture or panel describe a system that no longer
// exists?" — its corpus is diagrams and console copy, its evidence is deploy
// scripts and live AWS/GCP/Azure state, and its failure mode is DRIFT after a
// change that was itself correct. Folding them together would make both vaguer:
// one sweep with two evidence bases produces findings nobody knows how to act on.
//
// The pairing that matters is with the CLAUDE.md rule, not with the other sweep.
// The rule prevents drift at the moment of the change, while the author still
// remembers what moved. This catches what the rule missed, which is the only
// thing a periodic sweep can honestly claim to do.

const ASSERTIONS = {
  type: 'object',
  required: ['assertions'],
  properties: {
    assertions: {
      type: 'array',
      items: {
        type: 'object',
        required: ['where', 'claim', 'about'],
        properties: {
          where: { type: 'string', description: 'file + level/heading, e.g. "plan/09 L5.7" or "index.html OBS_ANALYST_DIAGRAM"' },
          claim: { type: 'string', description: 'what it asserts about how the lab is deployed or run' },
          about: { type: 'string', description: 'the component: a2alab-console, the brief watcher, the expiry collector, …' },
          surface: { type: 'string', description: 'plan | diagram | console-details | readme' },
        },
      },
    },
  },
}

const FINDING = {
  type: 'object',
  required: ['where', 'verdict', 'detail'],
  properties: {
    where: { type: 'string' },
    about: { type: 'string' },
    verdict: { type: 'string', description: 'accurate | stale | unverifiable' },
    detail: { type: 'string', description: 'what it says, what is actually true, and the evidence for the latter' },
    fix: { type: 'string', description: 'the specific edit — which file, which line, what it should say' },
  },
}

const DRAWIO_STATUS = {
  type: 'object',
  required: ['levels'],
  properties: {
    levels: {
      type: 'array',
      items: {
        type: 'object',
        required: ['level', 'drawn'],
        properties: {
          level: { type: 'string' },
          drawn: { type: 'boolean' },
          stale: { type: 'boolean' },
          missing_labels: { type: 'array', items: { type: 'string' } },
          svg_older_than_source: { type: 'boolean' },
        },
      },
    },
  },
}

const REDRAW = {
  type: 'object',
  required: ['level', 'redrawn', 'notes'],
  properties: {
    level: { type: 'string' },
    redrawn: { type: 'boolean', description: 'true once the .drawio matches the mermaid AND build succeeded' },
    notes: { type: 'string', description: 'what changed in the drawing, and anything you could not resolve' },
  },
}

const STAMPED = {
  type: 'object',
  required: ['stamped', 'checkOutput'],
  properties: {
    stamped: { type: 'array', items: { type: 'string' } },
    refused: { type: 'array', items: { type: 'string' }, description: 'levels stamp refused, with its reason' },
    checkOutput: { type: 'string', description: 'verbatim stdout of arch_drawio.py check' },
  },
}

const VERDICT = {
  type: 'object',
  required: ['stillWrong', 'why'],
  properties: {
    stillWrong: { type: 'boolean' },
    why: { type: 'string' },
  },
}

const EVIDENCE = `
Ground every judgement in what the repo and the clouds actually say, in this order:
  1. deploy/**/*.sh and deploy/**/*.py — what is CREATED, and where
  2. plan/09-deployment-map.md L6 — the code → deployment table
  3. live state, read-only: aws lambda get-function-configuration, aws ecs
     describe-services, aws scheduler list-schedules, aws events list-rules
  4. the code itself — an import, a subprocess call or a client construction
     says more about where something can run than any prose does
Never take one document as evidence for another: two docs can be wrong together,
and in this repo they usually drifted from the same change.
`.trim()

phase('Inventory')

const inventory = await agent(`
Inventory every ARCHITECTURAL ASSERTION in this repo — any statement about
where a component runs, what identity it uses, what it depends on, what
schedule fires it, or where its output is stored.

Look in:
  - plan/09-deployment-map.md — every level, including the mermaid sources
  - plan/05-observability.md, plan/01-architecture.md, plan/10-operations.md
  - config/diagrams.yaml
  - src/console/static/index.html — the *_DIAGRAM constants and every Details
    pane (BRIEF_META[].details, obsDashboardDetailsHtml, credsDetailsHtml,
    obsPipelineHtml). These are PUBLISHED copy: the console shows them to
    visitors, so a stale one is the console asserting something false.
  - README.md — the "Where it actually runs" section

Do NOT inventory docs/architecture/*.drawio or src/console/static/arch/*.svg:
they are draw.io twins of the plan/09 mermaid (D113), and their drift from it
is checked MECHANICALLY by the Drawings phase (scripts/arch_drawio.py), not
by judgement.

One entry per assertion, not per file. Prefer assertions that could plausibly go
stale: hosting, identity, schedule, storage, manual-vs-automatic. Skip pure
rationale ("why we chose X") — that is history and stays true.
`, { schema: ASSERTIONS, label: 'inventory' })

const assertions = (inventory && inventory.assertions) || []
log(`${assertions.length} architectural assertions to check`)

const findings = await pipeline(
  assertions,
  a => agent(`
Check ONE architectural assertion against reality.

WHERE: ${a.where}
SURFACE: ${a.surface || 'unknown'}
ABOUT: ${a.about}
CLAIMS: ${a.claim}

${EVIDENCE}

Decide: accurate | stale | unverifiable.

"stale" means the lab has MOVED ON and this still describes the old shape — a
component that has been hosted still called local, a manual step that now runs
on a schedule, a store that changed, an identity that changed. That is the
failure this sweep exists for.

"unverifiable" is a real answer when the evidence is not in the repo and you
cannot read the live state. Say so rather than guessing.

If stale, the fix must name the file and what the line should say instead.
`, { schema: FINDING, label: `check:${a.about}`.slice(0, 48), phase: 'Check' }),

  // Verify immediately, per assertion — a wrong "stale" finding sends someone
  // to edit a document that was right, which is worse than saying nothing.
  f => {
    if (!f || f.verdict !== 'stale') return f
    return agent(`
Try to REFUTE this claim of staleness. Default to refuted=true if uncertain.

CLAIMED STALE: ${f.where} — ${f.detail}

${EVIDENCE}

Refute it if: the document is describing a deliberately-kept local path (a
fallback, a dev loop, a fresh-checkout route); or the "current" behaviour you
would assert is itself only planned; or the assertion is about intent rather
than deployment. Confirm it only if a specific deploy script, live resource or
import proves the document describes something that is no longer how it works.
`, { schema: VERDICT, label: `verify:${(f.about || '').slice(0, 32)}`, phase: 'Verify' })
      .then(v => ({ ...f, confirmed: v ? v.stillWrong : false, refutation: v && v.why }))
  },
)

const flat = findings.filter(Boolean)
const stale = flat.filter(f => f.verdict === 'stale' && f.confirmed)
const unverifiable = flat.filter(f => f.verdict === 'unverifiable')

log(`${stale.length} confirmed stale · ${unverifiable.length} unverifiable · ${flat.length - stale.length - unverifiable.length} accurate`)

// THE DRAWINGS (D113). The phases above REPORT drift in the mermaid and copy —
// they do not edit plan/09, because a "stale" verdict is a judgement and the fix
// belongs to whoever owns the change. The draw.io twins are different: they have
// no facts of their own, only a layout of the mermaid's, so "does the drawing
// match the CURRENT mermaid?" is mechanical and safe to act on. Whatever the
// mermaid now says (fixed by hand after a previous sweep, or by the CLAUDE.md
// rule at change time), the drawing is redrawn to say the same.
//
// Redraws run in parallel — each touches only its own .drawio and .svg. Stamping
// is ONE agent at the end: `stamp` read-modify-writes the shared manifest.json,
// and parallel stamps would drop each other's entries.
phase('Drawings')
const dstatus = await agent(
  'Run `uv run python scripts/arch_drawio.py status --json` from the repo root and return its ' +
    'JSON array as `levels`, verbatim. Do nothing else.',
  { schema: DRAWIO_STATUS, label: 'drawio-status', agentType: 'general-purpose', effort: 'low' },
)
const dlevels = (dstatus && dstatus.levels) || []
const needs = dlevels.filter(
  l => l.drawn && (l.stale || (l.missing_labels || []).length || l.svg_older_than_source),
)
const undrawn = dlevels.filter(l => !l.drawn).map(l => l.level)
log(`drawings: ${needs.length} to redraw · ${undrawn.length} undrawn (mermaid-only)`)

const redraws = await parallel(
  needs.map(l => () =>
    agent(`
Bring ONE draw.io architecture drawing back in line with its mermaid source.

LEVEL: ${l.level}
STATUS: ${JSON.stringify(l)}

1. Read docs/architecture/STYLE.md — the style contract. Follow it exactly.
2. Read the "## ${l.level} —" level of plan/09-deployment-map.md; its FIRST mermaid
   fence is the source of facts. Read docs/architecture/${l.level}.drawio.
3. Edit the .drawio so it carries exactly the mermaid's nodes, edges, edge labels
   and subgraph titles — add what is new, REMOVE what the mermaid no longer has,
   relabel what changed. Preserve the existing hand layout wherever the facts did
   not change: this is a redraw of a diff, not a fresh drawing.
${(l.missing_labels || []).length ? `   The checker reports these mermaid labels missing from the drawing: ${JSON.stringify(l.missing_labels)}
` : ''}4. Render and LOOK: \`drawio -x -f png -s 1 -b 10 -o /tmp/arch-${l.level}.png docs/architecture/${l.level}.drawio\`,
   then Read the PNG. Fix overlaps, clipped labels, edges crossing nodes. Look again.
5. \`uv run python scripts/arch_drawio.py build ${l.level}\`
6. \`uv run python scripts/arch_drawio.py status --json\` — confirm missing_labels is
   empty for ${l.level}. (It will still say stale: stamping happens after you.)

Do NOT run \`stamp\`. Do NOT edit plan/09 or any file other than
docs/architecture/${l.level}.drawio and src/console/static/arch/${l.level}.svg.
If the mermaid itself looks wrong, say so in notes — the drawing follows it anyway.
`, { schema: REDRAW, label: `redraw:${l.level}`, phase: 'Drawings', agentType: 'general-purpose' }),
  ),
)
const redrawn = redraws.filter(r => r && r.redrawn).map(r => r.level)

let stampResult = null
if (redrawn.length) {
  stampResult = await agent(
    `Run from the repo root, in order:\n` +
      `1. \`uv run python scripts/arch_drawio.py stamp ${redrawn.join(' ')}\` — if it refuses a ` +
      `level, re-run stamp for the remaining levels one by one so one refusal does not block the rest.\n` +
      `2. \`uv run python scripts/arch_drawio.py check\`\n` +
      `Report which levels were stamped, which were refused (with the reason it printed), and the ` +
      `verbatim stdout of check. Change no files yourself.`,
    { schema: STAMPED, label: 'drawio-stamp', phase: 'Drawings', agentType: 'general-purpose', effort: 'low' },
  )
}

const result = {
  stale: stale.map(f => ({ where: f.where, about: f.about, detail: f.detail, fix: f.fix })),
  unverifiable: unverifiable.map(f => ({ where: f.where, detail: f.detail })),
  checked: flat.length,
  drawings: {
    redrawn: (stampResult && stampResult.stamped) || [],
    refused: (stampResult && stampResult.refused) || [],
    failed: redraws.filter(r => r && !r.redrawn).map(r => ({ level: r.level, notes: r.notes })),
    undrawn,
    notes: redraws.filter(Boolean).map(r => ({ level: r.level, notes: r.notes })),
    check: stampResult ? stampResult.checkOutput : 'nothing to redraw',
  },
}

// Persist this run to the gitignored archive. traces/ (never git) is the right
// home: a finding here can quote live ARNs / account ids from the cloud-state
// evidence above, which CLAUDE.md forbids committing. The workflow runtime has
// no fs and no clock, so a final agent stamps the time and writes the file.
phase('Log')
await agent(
  'Persist this "architecture-sweep" workflow run to the gitignored run-log archive. ' +
    'Do EXACTLY these steps, nothing else:\n' +
    '1. Run bash: `mkdir -p traces/workflows && date -u +%Y-%m-%dT%H-%M-%SZ`\n' +
    '2. Use the timestamp it prints as TS.\n' +
    '3. Use the Write tool to create `traces/workflows/architecture-sweep-<TS>.json` with EXACTLY ' +
    'the content between the markers, verbatim (no edits, no reformatting, and drop the marker lines):\n' +
    '===BEGIN===\n' +
    JSON.stringify(result, null, 2) +
    '\n===END===\n' +
    'Reply with only the path you wrote.',
  { phase: 'Log', label: 'log-run', agentType: 'general-purpose', effort: 'low' },
)

return result
