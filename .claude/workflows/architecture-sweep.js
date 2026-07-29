export const meta = {
  name: 'architecture-sweep',
  description: 'Check every architecture diagram and console Details pane against how the lab is actually deployed',
  whenToUse: 'After a component moves host, changes identity, or stops being manual: verify no diagram or console copy still describes the old shape',
  phases: [
    { title: 'Inventory', detail: 'collect every architectural assertion — diagrams, Details panes, plan prose' },
    { title: 'Check', detail: 'one agent per assertion, against deploy scripts and live infrastructure' },
    { title: 'Verify', detail: 'adversarial refutation before anything is reported as wrong' },
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

return {
  stale: stale.map(f => ({ where: f.where, about: f.about, detail: f.detail, fix: f.fix })),
  unverifiable: unverifiable.map(f => ({ where: f.where, detail: f.detail })),
  checked: flat.length,
}
