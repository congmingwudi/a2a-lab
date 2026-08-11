export const meta = {
  name: 'insights-audit',
  description: 'Actor-critic over config/insights.yaml: check each entry against its artifacts, then DEMOTE any tier the artifact no longer backs (WS20)',
  whenToUse: 'Before deck prep or publishing insights: confirm each measured/observed claim is still backed by a live artifact, and report the demotions',
  phases: [
    { title: 'Discover', detail: 'one item per insight with its claims and refs' },
    { title: 'Audit', detail: 'one agent per insight checking evidence against its cited sources' },
    { title: 'Verify', detail: 'adversarial refutation of claimed problems' },
    { title: 'Critic', detail: 'derive the artifact-backed tier and report demotions as the artifact' },
  ],
}

const INSIGHTS = {
  type: 'object',
  required: ['insights'],
  properties: {
    insights: {
      type: 'array',
      items: {
        type: 'object',
        required: ['id', 'status', 'claims', 'refs'],
        properties: {
          id: { type: 'string' },
          status: { type: 'string', description: 'measured | observed | hypothesis' },
          claims: {
            type: 'array',
            items: { type: 'string' },
            description: 'each checkable factual claim: numbers, dates, named incidents',
          },
          refs: { type: 'array', items: { type: 'string' } },
        },
      },
    },
  },
}

const FINDING = {
  type: 'object',
  required: ['id', 'verdict', 'detail', 'declared_tier', 'artifact_tier'],
  properties: {
    id: { type: 'string' },
    verdict: { type: 'string', enum: ['backed', 'problem'] },
    detail: { type: 'string' },
    evidence: { type: 'array', items: { type: 'string' }, description: 'file:line refs' },
    declared_tier: { type: 'string', enum: ['measured', 'observed', 'hypothesis'],
      description: "the status the entry currently declares" },
    // WS20: the tier the ARTIFACT supports, tested not asserted.
    //   measured   = a cited run id / trace / plan anchor still resolves AND (for a
    //                number) could be re-produced
    //   observed   = an artifact existed and is documented but is no longer
    //                reproducible (endpoint moved, credential rotated, trace pruned)
    //   hypothesis = no backing artifact resolves, or a cited ref is dead
    artifact_tier: { type: 'string', enum: ['measured', 'observed', 'hypothesis'],
      description: "the highest tier the still-resolving artifacts support (WS20 rule)" },
    dead_refs: { type: 'array', items: { type: 'string' },
      description: "cited refs (paths / D-numbers / run ids) that no longer resolve" },
  },
}

const VERDICT = {
  type: 'object',
  required: ['upheld', 'reason'],
  properties: {
    upheld: { type: 'boolean', description: 'true only if the problem is real and material' },
    reason: { type: 'string' },
  },
}

phase('Discover')
const found = await agent(
  'Read config/insights.yaml in this repo. For EVERY insight entry return ' +
  '{id, status, claims: [each checkable factual claim in headline/evidence AND advisory — ' +
  'numbers, dates, named incidents, "verified <date>" parentheticals], refs: [its refs list]}. ' +
  'All entries, no sampling.',
  { schema: INSIGHTS, effort: 'low' },
)
log(`${found.insights.length} insights to audit`)

const results = await pipeline(
  found.insights,
  ins =>
    agent(
      `Audit this A2A-lab insight for honesty: ${JSON.stringify(ins)}.\n` +
        'Rules of the lab:\n' +
        '(1) STATUS. "measured" claims must trace to recorded numbers somewhere in the lab ' +
        'record — plan/03-results.md, a dated ADR in plan/00-decisions.md, a measured ' +
        'subsection of plan/07-workstreams.md, plan/05-observability.md, or build-notes/ ' +
        '(several workstreams record their numbers in the workstream doc rather than in ' +
        '03-results, and that is legitimate — do NOT flag an insight merely for being backed ' +
        'outside 03-results). "observed" claims must be documented somewhere in plan/*.md or ' +
        'build-notes/.\n' +
        '(2) REFS. Every ref must be a path that EXISTS in the repo (or a D-number present in ' +
        'plan/00-decisions.md) and must actually discuss the topic. A ref to a file that is ' +
        'gone is a problem even when the claim itself is sound.\n' +
        '(3) SUPERSEDED FACTS. A claim can be true when written and false now (a config since ' +
        'fixed, a metric since wired up, a limit since lifted). Check the current state of the ' +
        'code and config it describes, not just the prose around it.\n' +
        '(4) ARTIFACT TIER (WS20 — the core check). The evidence tier is a property of the ' +
        'ARTIFACT, tested by whether it still resolves, NOT the label the author typed. Set ' +
        'declared_tier to what the entry says, then set artifact_tier to the HIGHEST tier the ' +
        'still-resolving artifacts actually support:\n' +
        '   • measured  — a cited run id / trace file / plan/*.md anchor still resolves AND (for ' +
        'a number) is re-producible in principle.\n' +
        '   • observed  — an artifact existed and is documented but is no longer reproducible ' +
        '(a beta endpoint moved, a credential rotated, a trace was pruned).\n' +
        '   • hypothesis — no backing artifact resolves at all, OR a cited ref is dead. A dead ' +
        'ref demotes even a sound-sounding claim.\n' +
        'List every dead/unresolvable ref in dead_refs. artifact_tier BELOW declared_tier is a ' +
        'demotion the critic will report.\n' +
        'Check each claim against the cited refs and the wider plan/ and build-notes/ ' +
        'directories. Verdict "problem" only for claims that are unbacked, contradicted, ' +
        'mis-statused, stale, or citing a dead ref — with file:line evidence.',
      { phase: 'Audit', label: `audit:${ins.id}`, schema: FINDING, effort: 'low' },
    ),
  f =>
    !f || f.verdict === 'backed'
      ? f
      : agent(
          `Adversarially verify this claimed insight problem — try to REFUTE it: ` +
            `${JSON.stringify(f)}. Reread the cited files and search plan/ yourself ` +
            '(evidence may live in a doc the insight forgot to ref — that softens the ' +
            'finding to a refs gap). Default to upheld=false if uncertain.',
          { phase: 'Verify', label: `verify:${f.id}`, schema: VERDICT },
        ).then(v => ({ ...f, upheld: v.upheld, reason: v.reason })),
)

const flat = results.filter(Boolean)
const confirmed = flat.filter(f => f.verdict === 'problem' && f.upheld)

// Critic phase (WS20). The artifact IS the number: report every entry whose
// artifact-supported tier is below its declared tier as a DEMOTION, the same
// trust-under-pressure move as the cost sentinel refusing a comparison it could
// not back (WS12/D44). A demotion is mechanical — driven by whether the cited
// artifact still resolves — not a judgment call, so it is reported here rather
// than folded into the "problem" verdict (a demotion is not always a defect:
// an endpoint that legitimately moved demotes measured→observed with no error).
phase('Critic')
const RANK = { measured: 3, observed: 2, hypothesis: 1 }
const demotions = flat
  .filter(f => f.artifact_tier && f.declared_tier && RANK[f.artifact_tier] < RANK[f.declared_tier])
  .map(f => ({
    id: f.id,
    from: f.declared_tier,
    to: f.artifact_tier,
    dead_refs: f.dead_refs || [],
    why: f.detail,
  }))
const deadRefCount = demotions.reduce((n, d) => n + (d.dead_refs?.length || 0), 0)
// The one-line artifact, the shape the working note asked for:
// "demoted N of M ... caught K citing a run that no longer exists".
const artifact =
  `The critic demoted ${demotions.length} of ${flat.length} insights ` +
  `(${demotions.filter(d => d.to === 'hypothesis').length} to hypothesis), ` +
  `and caught ${deadRefCount} citing a ref that no longer resolves.`
log(`${flat.length} audited · ${confirmed.length} confirmed problems · ${demotions.length} demotions`)
log(artifact)
return {
  audited: flat.length,
  backed: flat.filter(f => f.verdict === 'backed').length,
  confirmed_problems: confirmed,
  demotions,
  artifact,
}
