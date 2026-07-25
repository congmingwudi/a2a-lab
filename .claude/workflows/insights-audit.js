export const meta = {
  name: 'insights-audit',
  description: 'Verify every config/insights.yaml entry: measured numbers vs recorded runs, refs vs the docs they cite, status honesty',
  whenToUse: 'Before deck prep or publishing insights: confirm each measured/observed claim is still backed by the lab record',
  phases: [
    { title: 'Discover', detail: 'one item per insight with its claims and refs' },
    { title: 'Audit', detail: 'one agent per insight checking evidence against its cited sources' },
    { title: 'Verify', detail: 'adversarial refutation of claimed problems' },
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
  required: ['id', 'verdict', 'detail'],
  properties: {
    id: { type: 'string' },
    verdict: { type: 'string', enum: ['backed', 'problem'] },
    detail: { type: 'string' },
    evidence: { type: 'array', items: { type: 'string' }, description: 'file:line refs' },
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
  '{id, status, claims: [each checkable factual claim in headline/evidence — numbers, ' +
  'dates, named incidents], refs: [its refs list]}. All entries, no sampling.',
  { schema: INSIGHTS, effort: 'low' },
)
log(`${found.insights.length} insights to audit`)

const results = await pipeline(
  found.insights,
  ins =>
    agent(
      `Audit this A2A-lab insight for honesty: ${JSON.stringify(ins)}.\n` +
        'Rules of the lab: status "measured" claims must trace to recorded numbers in ' +
        'plan/03-results.md or a dated ADR in plan/00-decisions.md; "observed" claims must ' +
        'be documented somewhere in plan/*.md; refs must point at docs/ADRs that actually ' +
        'discuss the topic. Check each claim against the cited refs and the wider plan/ ' +
        'directory. Verdict "problem" only for claims that are unbacked, contradicted, or ' +
        'mis-statused (e.g. measured without a number on record) — with file:line evidence.',
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
log(`${flat.length} audited · ${confirmed.length} confirmed problems`)
return {
  audited: flat.length,
  backed: flat.filter(f => f.verdict === 'backed').length,
  confirmed_problems: confirmed,
}
