# The `.env` problem — what a solo lab and a platform team actually share

**Feature area:** secret distribution, environment identity, and the guardrails
that make "remove the account name from the repo" safe rather than reckless.
Built 2026-07-27 in one sitting, after a routine question — *should the public
repo name my employer's AWS account?* — turned out to have five answers.

## Engineering takeaway

A credential store solves *where secrets live*. It does not solve the three
failures that actually bite: **deploying to the wrong account**, **a config
value that is correct on your machine and wrong on everyone else's**, and **an
API quietly serving the identifiers you just removed from the repo**. Fix them
together, or the first fix makes the rest more likely and harder to see.

## The story

The lab had a rule — every credential is a service identity fetched with one
human login (D39) — and one file that had always broken it. `.env` held every
platform's keys, the AWS account id, the GCP project id, and the Salesforce
consumer secrets, in plaintext, on exactly one laptop. Losing it would not lose
the code. It would lose the ability to *run or deploy* the code, which across
five clouds is most of the value.

The trigger for fixing it was unrelated: the repo is public, and it named the
employer-provided AWS account (`<12-digit id>`, SSO profile `<name>`, and the
SSO domain, which is the association that actually identifies the company).
That is a compliance question, not an engineering one. But the fix has an
engineering consequence that is easy to miss:

> **Removing the account label makes deploying to the wrong account *easier*.**
> The label was doing accidental duty as a sanity check. Take it out and add
> nothing, and the next forgotten `AWS_PROFILE` silently creates real, billable
> infrastructure in a personal account — and stays quiet about it.

So the scrub shipped with a guard, not after it.

## The five pieces, and why each one is not optional

**1. Secrets move through the store you already trust.** `scripts/env_sync.py
pull | push | diff` puts `.env` in AWS Secrets Manager next to every other lab
credential. No new vendor, no new human login — the AWS SSO session is still the
only one. Onboarding becomes **clone → `aws sso login` → `env_sync.py pull`**.

The refusals are the design, not the feature list. `pull` will not overwrite a
diverged `.env` without `--force` and always keeps a timestamped backup; `push`
will not drop keys the secret already has; `diff` reports key **names** and
which side is ahead, never values. A tool that can silently destroy the only
copy of every credential is a tool that eventually will.

**2. `.env.example` is the contract; the secret holds only values.** The
checked-in example names every key and explains what it is for. That split is
what makes the secret shareable: a teammate reads the contract in the repo and
receives values only if IAM already admits them. It is also what makes review
possible — a new key shows up in a pull request as a documented line, not as an
opaque blob that changed.

**3. No environment identifier is hardcoded — including fallbacks.** The
non-obvious half. This was in the repo:

```sh
PROJECT="${GOOGLE_CLOUD_PROJECT:-a2a-lab-d441}"
```

It reads as a sensible default and behaves correctly on the machine it was
written on, which is exactly why it survives review. On anyone else's machine it
silently targets someone else's project. **A `:-` fallback is a hardcode that
only reveals itself to the next person.** The rule now is
`${VAR:?set VAR in .env}` — fail at the top, loudly, rather than proceed against
the wrong cloud. Same for Python: no literal defaults for account or project
ids.

**4. The deploy proves its target before creating anything.**
`deploy/aws_preflight.sh` is sourced by every deploy script that calls the AWS
CLI. It resolves the session with `sts get-caller-identity` and refuses unless
it matches `A2ALAB_AWS_ACCOUNT_ID` from `.env`:

```
aws-preflight: WRONG ACCOUNT — refusing to deploy.
    expected : <lab account>   (A2ALAB_AWS_ACCOUNT_ID in .env)
    session  : <personal account>
    identity : arn:aws:sts::<personal account>:assumed-role/...
```

It also pins `AWS_REGION` and `AWS_DEFAULT_REGION` together, because boto3
prefers the latter and an ambient value exported by the operator's shell had
already misdirected three separate components.

**5. The API is the boundary, not the repo.** This one was found by accident,
and it is the piece most likely to generalise.

With the repo clean, setting the Foundry console URL meant putting an Azure
tenant id into `.env` — fine, since `.env` is gitignored. Checking *where that
value ends up* showed the console's `/api/scenarios` is unauthenticated (the
public landing page renders from it) and was already serving the Salesforce
org's my-domain three times over, with the GCP project id on the same path.

**The scrub had removed those identifiers from source while an endpoint kept
serving them**, because the endpoint assembled them at runtime from `.env`. No
amount of grepping the repo would have found it.

Component deep links now resolve for signed-in callers only. Anonymous visitors
keep the titles, notes and screenshots — the actual exhibit — and the button
reads *"sign in to open"* rather than *"not yet available"*, because the second
would be a claim about the lab instead of a statement about the viewer.

The rule to carry: **anything derived from configuration has to be checked at
the edge it is served from.** Static scanning finds literals; it cannot find a
value your own code assembles.

## The distinction that makes stripping safe

Not every identifier can go, and knowing which is which is the whole job.

The Foundry portal URL carried `?tid=<tenant-id>` — a **sign-in hint**. The
browser does not need it when the session is already in that tenant, so it was
stripped. `AZURE_TENANT_ID` stayed in `.env`, because the Entra service
principal genuinely authenticates with it.

Same identifier, same file, two different roles: **auth configuration versus URL
decoration.** The test is whether anything stops working without it — and if you
cannot answer that, you are not ready to remove it. Removing a load-bearing id
to feel safer is how a security cleanup becomes an outage.

## What made it stick: the checks, not the intentions

Four tests, and three of them failed usefully on the first run:

- **No SSO profile name in any tracked file.**
- **No AWS account id in any tracked file** — scoped to digits appearing in
  account context, so fixtures and timestamps do not trip it.
- **Every deploy script that calls the AWS CLI sources the preflight.** Written
  as a behavioural rule rather than a list, so a *new* script is covered
  automatically. It immediately flagged `deploy/bridge/gcp_federation.sh` —
  which turned out to be pure `gcloud` and correctly exempt, and that is what
  moved the rule from "these six files" to "anything calling `aws`".
- **The public API returns no component deep links** — the check that would
  have caught piece 5 before it shipped, had it existed first.

The checks run over `git ls-files`, so they fail on the way **in**. That timing
is the whole point: before a push, removing an identifier is an edit. After a
push, it is a history rewrite.

## The part that does not fit on a slide, and should be said anyway

Scrubbing HEAD does not erase history. The identifiers were in 10 commits. The
repo had exactly one clone, on one machine, with no forks — so
`git filter-repo` plus a force-push was cheap and was the right call. **That
is a fact about this repository, not a general recommendation.** With one fork,
one CI system, or one colleague's clone, the same operation breaks other
people's work and leaves the old objects reachable anyway. Establish the
boundary check first; treat history rewriting as the exception you can only
afford early.

## How this maps to a platform team

Nothing above is solo-scale. The same four pieces are what a team needs, with
the owners redistributed:

| Lab (one person) | Team | Same reason |
|---|---|---|
| `.env` in Secrets Manager, pulled by the operator | per-environment secrets, pulled by role | secrets ride the identity system, not a message |
| `.env.example` in git | the same file, reviewed in PRs | the contract is public, the values are not |
| `A2ALAB_AWS_ACCOUNT_ID` in `.env` | per-environment account ids in the pipeline | the deploy proves its target instead of trusting the operator |
| `aws_preflight.sh` sourced by each script | the same check in CI, before plan/apply | the guard belongs where the deploy happens |
| tests over `git ls-files` | the same tests, plus secret scanning at the PR gate | catch it on the way in, not in the audit |
| public API returns no identifiers | the same check on any unauthenticated endpoint | a repo scrub is not a boundary; the API is |

The honest difference is blast radius, not practice: a solo lab loses a laptop,
a team loses an environment. The reason the practices look identical is that
both failures come from the same source — **configuration that is correct in
exactly one place**.

## The follow-up: the store covered one file, and there were twenty

Everything above is about `.env`. That framing survived the whole build without
being questioned, and it was wrong by about twenty files.

`.env` went into Secrets Manager. Still sitting single-copy on the same laptop:
the lab's JWT signing key, the Cloudflare origin private key, four production
Salesforce consumer keys, the two bearer tokens that authenticate the MCP
endpoints — and, outside this repo entirely, nine other projects' `.env` files
and the SSH key used to reach every one of those repos. **The note's own
closing line — *configuration that is correct in exactly one place* — applied to
the note.** Fixing the file that had a rule written about it is not the same as
fixing the class.

The answer is chezmoi with age encryption into a private dotfiles repo (D45),
with the tooling in a second repo so a new machine can read *how it works*
before it can decrypt anything. Secrets are encrypted at rest in the repo, so
losing the repo is not losing the credentials — losing the age key is, and that
key has exactly one other copy.

**It costs the one-login rule, and that should be said out loud.** D39's whole
point was that AWS SSO is the only human login. The age key is a second one. It
buys coverage of everything SSO cannot bootstrap — most pointedly the SSH key
you would need to reach the repo that would otherwise hold it. A rule worth
having is worth breaking on purpose, once, with the reason written down.

### The part worth a slide: filenames are a static scan

Classifying twenty-odd files as encrypt-or-plain meant opening each one. Four
were not what their names said:

| File | Reads as | Actually holds |
|---|---|---|
| `fanout_mcp.json`, `obs_mcp.json` | MCP endpoint config | live bearer tokens |
| `bridge_host.json`, `acm_arn.txt` | a hostname, a cert reference | ARNs carrying the AWS account id |
| `f6-eca-wiring.md` | wiring notes — its own header says *"Secrets are NOT here"* | four production consumer keys |

That header is the interesting one. It was **literally true** — consumer
*secrets* really are not in the file — and it read as *this file is safe*, which
is how it nearly went into a plaintext backup. A caveat that is accurate and
misleading is worse than no caveat, because it survives review.

This is piece 5 of the five, one layer out. There, the repo was clean and the
API served the identifiers anyway. Here, the filename was clean and the file
held the token anyway. **Both are the same mistake: checking the description of
a thing instead of the thing.** Fixed at the source rather than in a
wiki — `deploy_fanout.sh` now prints *"saved with its bearer token … — secret"*,
and that header no longer claims safety.

### And the audit recreated what the scrub removed

The write-up of these findings stated the AWS account id in full. It lives in
`tmp-docs/`, which is gitignored, so nothing was exposed — but note *why* the
guard was never going to catch it: `test_no_account_identifiers.py` walks
`git ls-files`. Untracked file, invisible to the check, by construction.

The document describing the identifier cleanup was the one place the identifier
got rewritten in plaintext. Not ironic — structural. **A boundary check covers
the surface it is pointed at, and the artifacts *about* a cleanup are reliably
outside it.**

### The gap that is a process, not a control

The daily sync runs `chezmoi re-add`: content changes to **already-tracked**
files. A brand-new secret under `.a2alab/` is silently unprotected until someone
runs `chezmoi add --encrypt` once. Nothing detects that. The discovery workflow
(drop a path in `to-track.md`, review, tag, register) is the answer, and it is a
process answer — which by this note's own standard is the weakest kind. Recorded
as a known limit rather than dressed up as a control.

## Evidence and limits

- **Repository-backed:** `scripts/env_sync.py` and its seven safety tests,
  `deploy/aws_preflight.sh`, `tests/unit/test_no_account_identifiers.py`, and
  the `.env.example` contract are all inspectable.
- **Verified end to end:** the preflight's four branches were exercised against
  a stubbed CLI — matching account proceeds, mismatch refuses with both ids
  shown, unset warns and proceeds, no session refuses. The "no session" branch
  was confirmed for real: an expired SSO token produced a refusal rather than a
  fall-through to the default profile.
- **Verified live 2026-07-27:** the round trip. `push` created the secret from
  80 keys, `diff` reported *identical*, `pull` was a no-op, and adding one key
  locally produced the refusal naming that key rather than an overwrite.
- **A demonstration arrived unprompted during that check.** `env_sync.py` wrote
  the secret to `us-east-1` (from `.env`), and the next bare `aws
  secretsmanager describe-secret` reported **ResourceNotFoundException** —
  because the shell exported `AWS_DEFAULT_REGION=us-west-2`, the SSO home
  region. Same account, same credentials, same secret name, wrong region, and
  the error says *"can't find the specified secret"* — which reads as "the push
  failed". This is the fourth time that variable has misdirected a component in
  this lab, and it is why the preflight pins `AWS_REGION` and
  `AWS_DEFAULT_REGION` together instead of assuming the shell is neutral.
- The backup script (`~/projects/local-project-files/sync.sh`) is a
  same-machine copy — insurance against `rm -rf`, not against losing the
  machine. **Superseded later the same day** by the chezmoi setup below (D45);
  it remains as the fast local undo.

## Put this in the presentation

**Slide headline:** A secret store is one of five fixes, and the other four are
where the outages come from.

| Fix | Stops |
|---|---|
| Secrets in the credential store | "it only exists on my laptop" |
| `.example` contract in git | "what is this key for?" |
| No hardcoded ids — **including `:-` fallbacks** | "it works on my machine" |
| Preflight account check | "we deployed to the wrong account" |
| Checking the API, not just the repo | "we removed it from git and kept serving it" |

**Visual:** the `${VAR:-project-id}` line with the fallback circled, captioned
*"a hardcode that only shows up on someone else's machine."*

**Speaker note:** open with the compliance question, because it is the one the
room recognises, then pivot on the sentence that reframes it — *removing the
account name from the repo makes deploying to the wrong account easier.* That is
the moment the talk stops being about secret hygiene and starts being about
guardrails. Close on the timing point: these checks run over `git ls-files` so
they fail before a push, when the fix is still an edit rather than a history
rewrite.

**Optional second visual** (use if the room is engineers rather than
leadership): the `f6-eca-wiring.md` header — *"Secrets are NOT here"* — over a
table of four live consumer keys. Caption: *"accurate, and it nearly put this in
a plaintext backup."* It lands faster than the API story and makes the same
point, and it pairs with the `${VAR:-project-id}` slide as a matched set: two
things that are correct as written and wrong as read.

**Speaker note — chezmoi, the details behind that slide.** Details for the
follow-up question, not the slide itself:

- **Two repos, on purpose.** `congmingwudi/dotfiles` (private) is the chezmoi
  source — every tracked config, age-encrypted where sensitive, at
  `~/.local/share/chezmoi`. `congmingwudi/chezmoi-dotfiles-setup` is the
  tooling and the written workflow. Split so a new machine can read *how the
  thing works* before it is able to decrypt anything.
- **age, one key, two copies.** Private key at `~/.config/chezmoi/key.txt`,
  0600; the only other copy is a 1Password secure note. Public key is baked
  into `.chezmoi.toml.tmpl`, so `chezmoi init` self-configures encryption on a
  new machine. Losing both copies loses every encrypted secret permanently —
  that is the honest failure mode, and it is why the answer to "why not more
  redundancy" is 1Password rather than a third repo.
- **Recovery, end to end:** `brew install chezmoi age` → restore the key from
  1Password → `chezmoi init --apply <dotfiles repo>`. `.a2alab/` comes back
  fully populated at its exact original path on a machine that never cloned
  this repo. `.env` still comes from Secrets Manager (`aws sso login` →
  `env_sync.py pull`) — the two paths are independent by design, so neither is
  a single point of failure for the other.
- **Daily sync is `chezmoi re-add` + commit + push**, launchd at 18:00, logs in
  `~/Library/Logs/chezmoi-sync/`. Tracked files only — that is the gap above,
  and if asked "how would you productionise this", the honest answer is that
  the discovery step is where a team would put a scanner rather than a
  markdown file.
- **What is deliberately not backed up:** `cma_sessions.json` (1008-entry
  session log), `openai_responses.json`, and the regenerated expiry/brief
  reports. Generated output, not authored config — including them would churn
  the backup on every run and teach everyone to ignore its diffs. Worth saying
  because "back up everything" is the instinct and it is how a backup becomes
  noise.
- **If asked why not Secrets Manager for all of it:** it would work, and it
  would mean a `.pem` per secret plus a bootstrap ordering problem for the SSH
  key that reaches the repos. The deciding factor was that most of this
  material is *files at paths*, not values — chezmoi restores the path, which
  is the actual unit of recovery here.

## Postscript: we built the agent, then removed it

The credential analyst — the layer that reads the measured expiry report and
says what to rotate first — was built as a **Claude Managed Agent** and worked
on the first run. It found things a threshold could not: that a GCP key and an
Entra secret expire within days of each other next July and are therefore one
coordinated rotation, and that the TLS certificate is imported from Cloudflare
so rotating it means keeping two sides in sync.

Then the shape was checked against what Managed Agents are *for*:

| Managed Agents provides | used by this analyst |
|---|---|
| hosted tool execution (MCP, custom tools) | no — it has no tools |
| scheduled deployments | no — collection needs the operator's own cloud sessions |
| multi-turn session state | no — one shot |
| managed sandbox / environment | no |

None of it. The cost was not theoretical: an agent object to create and version,
a setup step, a state file, `sessions.create` + `events.send` + an event stream
with idle detection — and a bug that existed *only* because of the extra surface
(`sessions.messages` does not exist; the kickoff has to be streamed, which broke
the first run). Replacing all of it with one `messages.create` removed 50 lines
and a setup command, and produced the same analysis from the same model over the
same data.

**The rule this earns:** reach for the agent abstraction when you need what it
does — **tools, scheduling, or durable multi-turn state** — not because the work
involves a model. "It uses Claude" is not an argument for an agent framework any
more than "it uses SQL" is an argument for an ORM.

**And the condition that would reverse it, kept because it is plausible:** the
analyst becomes a Managed Agent the moment collection moves off the laptop. A
Lambda with a task role could read IAM and ACM directly and reach Entra and GCP
through the federation the lab already runs; once dates land in a store on a
schedule, the analyst no longer needs a person, and a scheduled deployment
reading through MCP is exactly the right shape — plus it could then *correlate*
("telemetry stopped three days ago; the metrics key expired three days ago"),
which is a join a prompt cannot do. The console's Credentials → Details tab
carries that sketch as a second diagram labelled *potential*, so the option is
documented rather than forgotten.

**Why this belongs in the deck.** Most agent talks show the build. This is the
more useful artifact: a team that can *remove* an agent has a real test for when
to add one. The test here fits on a slide — tools, schedule, or state; otherwise
it is an API call.
