export const meta = {
  name: 'matrix-honesty-sweep',
  description: 'Cross-check every claimed cell in plan/02-matrix.md against targets.yaml, recorded results, and code',
  whenToUse: 'Before demos or publishing the matrix: verify it claims nothing the lab cannot back with config, measured runs, or code',
  phases: [
    { title: 'Discover', detail: 'parse the matrix + registry into one claim per cell' },
    { title: 'Audit', detail: 'one agent per cell cross-checking status vs evidence' },
    { title: 'Verify', detail: 'adversarial refutation of claimed discrepancies' },
  ],
}

const CLAIMS = {
  type: 'object',
  required: ['cells'],
  properties: {
    cells: {
      type: 'array',
      items: {
        type: 'object',
        required: ['cell', 'claim'],
        properties: {
          cell: { type: 'string', description: 'direction × protocol, e.g. "agentforce→claude REST"' },
          claim: { type: 'string', description: 'claimed status plus any latency/notes shown in the matrix' },
        },
      },
    },
  },
}

const FINDING = {
  type: 'object',
  required: ['cell', 'verdict', 'detail'],
  properties: {
    cell: { type: 'string' },
    verdict: { type: 'string', enum: ['consistent', 'discrepancy'] },
    detail: { type: 'string' },
    evidence: { type: 'array', items: { type: 'string' }, description: 'file:line refs' },
  },
}

const VERDICT = {
  type: 'object',
  required: ['upheld', 'reason'],
  properties: {
    upheld: { type: 'boolean', description: 'true only if the discrepancy is real and would mislead a reader' },
    reason: { type: 'string' },
  },
}

phase('Discover')
const found = await agent(
  'In this repo, read plan/02-matrix.md (the honest protocol matrix, including its findings ' +
  'ledger) and config/targets.yaml. Produce one item per matrix cell or ledger claim: ' +
  '{cell: "<direction × protocol>", claim: "<claimed status + any latency numbers or notes>"}. ' +
  'Cover ALL cells and ledger rows — do not sample or cap.',
  { schema: CLAIMS, effort: 'low' },
)
log(`${found.cells.length} matrix claims to audit`)

const results = await pipeline(
  found.cells,
  c =>
    agent(
      `Audit this A2A-lab matrix claim for honesty: ${JSON.stringify(c)}.\n` +
        'Cross-check three sources in this repo:\n' +
        '(1) config/targets.yaml — does the target exist, with the claimed status ' +
        '(native / via-bridge / via-shim / blocked-beta)?\n' +
        '(2) plan/03-results.md — is every latency or measured number in the claim backed ' +
        'by a recorded run there (or in plan/00-decisions.md)?\n' +
        '(3) src/ and config/scenarios.yaml — do the client/server/platform components the ' +
        'claim implies actually exist?\n' +
        'Report verdict "discrepancy" only for real mismatches a reader would be misled by, ' +
        'with file:line evidence; stylistic drift is "consistent".',
      { phase: 'Audit', label: `audit:${c.cell}`, schema: FINDING, effort: 'low' },
    ),
  f =>
    !f || f.verdict === 'consistent'
      ? f
      : agent(
          `Adversarially verify this claimed matrix discrepancy — try to REFUTE it: ` +
            `${JSON.stringify(f)}. Reread the cited files yourself. Uphold only if the ` +
            'mismatch is real and material; default to upheld=false if uncertain.',
          { phase: 'Verify', label: `verify:${f.cell}`, schema: VERDICT },
        ).then(v => ({ ...f, upheld: v.upheld, reason: v.reason })),
)

const flat = results.filter(Boolean)
const confirmed = flat.filter(f => f.verdict === 'discrepancy' && f.upheld)
log(`${flat.length} audited · ${confirmed.length} confirmed discrepancies`)
return {
  audited: flat.length,
  consistent: flat.filter(f => f.verdict === 'consistent').length,
  confirmed_discrepancies: confirmed,
}
