# Operations — running the lab once it is deployed

**What this file is for.** `plan/09-deployment-map.md` answers *what is deployed,
where, and why there*. This one answers *what do I do now* — the procedures that
come up while operating a lab that is already running. Each entry says what to
run, what to expect, and how to back out.

**The audience is the operator, six months later.** Every procedure here was
either performed or discovered on 2026-07-28, when the lab moved off the laptop
(WS13). Anything not yet performed says so.

- [Rotate the console personas' passwords](#rotate-the-console-personas-passwords)
- [Rotate the lab JWT keypair](#rotate-the-lab-jwt-keypair)
- [Rotate a platform credential](#rotate-a-platform-credential)
- [Deploy a code change vs a config change](#deploy-a-code-change-vs-a-config-change)
- [Move the brief watcher between hosts](#move-the-brief-watcher-between-hosts)
- [Keep the demo warm before a live run](#keep-the-demo-warm-before-a-live-run)
- [Sign off an insight, and keep the repo copy](#sign-off-an-insight-and-keep-the-repo-copy)
- [Iterate on the console without deploying](#iterate-on-the-console-without-deploying)
- [When the console looks broken](#when-the-console-looks-broken)
- [Why credentials are read at container start, not per request](#why-credentials-are-read-at-container-start-not-per-request)

---

## Rotate the console personas' passwords

Login is persona + that **role's** shared password (D36): `ryan` is the lab
owner (role `master of the universe`, password `A2ALAB_MASTER_PASSWORD`), `ana`
is an operator (`A2ALAB_OPERATOR_PASSWORD`), `vic` is a viewer
(`A2ALAB_VIEWER_PASSWORD`), per `config/users.yaml`. The owner role is distinct
from operator *only* so the operator password can be handed to colleagues
without also handing out the owner's login (D36) — same permissions, separate
credential.

```sh
# 1. change the values
#    A2ALAB_MASTER_PASSWORD= / A2ALAB_OPERATOR_PASSWORD= / A2ALAB_VIEWER_PASSWORD=   in .env

# 2. keep the credential store in step (D39)
uv run python scripts/env_sync.py push

# 3. push them to the running console
deploy/console/deploy_console.sh --skip-build
```

**Why step 3 is needed today**, and it is not the reason you would guess: the
passwords are not read from `a2alab/env/dev` at request time — they arrive in
the console's own scoped secret `a2alab/runtime/console`, and **that secret is
built by the deploy script**. `env_sync push` updates the whole-`.env` secret,
which the console never reads. See
[the last section](#why-credentials-are-read-at-container-start-not-per-request)
for why it is scoped that way, and what it would take to drop step 3.

`--skip-build` is correct here: this is a config change, and the secret block
runs regardless. Existing sessions **survive** — the JWT signing key is
untouched, so tokens issued before the rotation stay valid until they expire.

**Verify:**

```sh
set -a; source .env; set +a
curl -s -X POST "https://$CONSOLE_HOSTNAME/api/login" -H 'Content-Type: application/json' \
  -d "{\"username\":\"ryan\",\"password\":\"$A2ALAB_MASTER_PASSWORD\"}" | head -c 120
```

Expect a `token` and `user: {sub, name, role}`. A `401` with the *old* password
is the confirmation that matters.

---

## Rotate the lab JWT keypair

Different blast radius from a password: **every issued token dies immediately**,
because the key that signed them is gone. Do this only on suspected compromise.

```sh
rm .a2alab/lab_jwt_private.pem .a2alab/lab_jwt_public.pem
uv run python -c "import sys; sys.path.insert(0,'src'); from interop import identity; identity.ensure_keypair()"
deploy/console/deploy_console.sh --skip-build     # ships BOTH halves in the secret
```

The console **issues** tokens, so it holds the private half — unlike a seam that
only verifies. Both halves travel in `a2alab/runtime/console`, never on the task
definition. If the private key is missing the container generates its own and
the failure is invisible: login succeeds, then every request 401s with
`InvalidSignatureError` underneath (D53).

`.a2alab/lab_jwt_private.pem` is age-encrypted into the chezmoi dotfiles repo
(D45) — re-run the chezmoi sync after rotating, or the backup holds a key that
no longer opens anything.

---

## Rotate a platform credential

Anything in `.env` that a hosted seam uses — `ANTHROPIC_API_KEY`,
`SF_CLIENT_SECRET`, the Azure trio, `BRIDGE_TOKEN`, `A2ALAB_TOKEN`.

```sh
# .env, then:
uv run python scripts/env_sync.py push
# then re-run the deploy for EVERY seam that carries it (--skip-build is fine):
deploy/console/deploy_console.sh --skip-build
deploy/faces/deploy_faces.sh     --skip-build
deploy/briefs/deploy_briefs.sh
deploy/bridge/deploy_bridge.sh   --skip-build
```

**Which seams carry which credential is not obvious**, and getting it wrong
leaves one container on the old value. The authority is the `keys = [...]` list
at the top of each deploy script. `A2ALAB_TOKEN` is in all four; the Salesforce
pair is in console, faces, briefs and bridge; `ANTHROPIC_API_KEY` is in all four
(console, faces, briefs and bridge — the bridge carries it too,
`deploy/bridge/deploy_bridge.sh`).

**A credential that is excluded from the task definition but not added to that
list is simply deleted.** That is how the hosted console ended up with no
persona passwords at all: the pattern rule correctly kept `*_PASSWORD` out of
the plain env, and nothing put it anywhere else (D53).

---

## Deploy a code change vs a config change

| Changed | Command | Why |
|---|---|---|
| `src/**`, `config/**`, a Dockerfile | full deploy, no flag | `COPY src ./src` and `COPY config ./config` bake these into the image |
| an env var, a rotated credential | `--skip-build` | the task definition, env and secret are rewritten either way |

`--skip-build` only skips the image. A code change deployed with it looks
deployed and runs yesterday's code — the env is right and the code reading it is
stale, which reads as a code bug. This cost three debugging rounds in one day
(a renamed env var, a new `targets.yaml`, a new `identity.PRIVATE_KEY_ENV`).

**When a fix does not take effect, check the image before re-reading the code:**

```sh
aws ecs describe-task-definition --region us-east-1 --task-definition a2alab-console \
  --query 'taskDefinition.containerDefinitions[0].image' --output text
# then confirm the code is actually in it:
docker run --rm <image> grep -c PRIVATE_KEY_ENV /app/src/interop/identity.py
```

---

## Move the brief watcher between hosts

The watcher services scheduled brief sessions that stall awaiting a host-side
Salesforce write. **Exactly one may run.** Two racing for the same session is
the one way to deliver a brief twice.

Before starting a watcher anywhere it has not run before:

```sh
# seed the hosted store from the local file, as a UNION (needs the WRITER role)
A2ALAB_PG_SECRET_ARN="$A2ALAB_PG_WRITER_SECRET_ARN" \
  uv run python -m briefs --push-state
```

**Skipping this costs real money.** The serviced-session set lives in
`lab.lab_state`, and a fresh store is empty — so the watcher re-services every
session still listed in recent deployment runs. On 2026-07-28 that was eight
days of them at ~10 minutes of web research each. Nothing was double-delivered
only because those tool calls had already been consumed (which is why the first
one reported `0 brief(s)`).

Stop the hosted watcher, or scale it to zero while working locally:

```sh
aws ecs update-service --region us-east-1 --cluster a2alab --service a2alab-briefs --desired-count 0
```

Nothing is lost while it is down — sessions idle awaiting the tool result and
are picked up on the next poll.

---

## Keep the demo warm before a live run

The hosted twins cold-start ~31–56s (`config/targets.yaml`), and a cold face
mid-demo blows the Path-A action budget — Agentforce returns 200 with an *empty*
delegated section, which looks like a bug on stage. So before an attended demo,
keep them warm:

```sh
uv run python scripts/demo_watch.py            # one warm pass over every warmup:true target
uv run python scripts/demo_watch.py --json     # machine-readable
uv run python scripts/demo_watch.py --targets claude-agentcore,openai-agentcore
```

One pass is one-shot. To hold the lab warm for the length of a demo, wrap it in a
**bounded, session-scoped loop** from inside a live Claude Code session:

```
/loop 8m uv run python scripts/demo_watch.py
```

`/loop` is a Claude Code session command — it re-runs the command every 8 minutes
**in the current session**, so it stops the moment you close the terminal. That is
exactly right for a pre-demo/attended watch and exactly **wrong** for standing
monitoring: an unattended, always-on watch is not this command's job. That job
belongs to the scheduled deployments — the obs analyst (D23) and the cost sentinel
(WS12/D44), EventBridge-fired Lambdas that run with no human present. Use `/loop`
when you are *there*; use a schedule when you are not.

**What it actually does, and why it is a thin wrapper.** The console already knows
how to warm a target: `POST /api/warmup/{name}` composes the correct delegated
ping and records the duration to `warmups.jsonl` for the cross-platform cold-start
comparison. `demo_watch.py` only drives those existing endpoints — it discovers
the warmable set from `GET /api/warmup`, fires each, and checks `/healthz`. It
reimplements no warm-up logic, because a second copy would fork the very numbers
the console publishes. A failed warm-up exits non-zero, so a Stop-hook wired to
Slack turns each loop pass into a walk-away signal.

**Point it at the right console.** It defaults to `http://localhost:8200` (the
`run_console.sh` port); set `A2ALAB_CONSOLE_URL` to the hosted console to warm the
deployed twins. When the target console has `A2ALAB_TOKEN` set, `/api/warmup` is
gated — the script sends that token as `X-Lab-Token` (it loads `.env`, so a value
there is picked up; a `401` means the token it sent does not match the console's).

---

## Sign off an insight, and keep the repo copy

Sign-offs are stored in Aurora when hosted (`lab.lab_state`), because the file
they used to live in is a layer of the container image (D50).

```sh
# after approving in the console:
uv run python scripts/insight_reviews_sync.py pull    # store -> config/insight_reviews.yaml
git add config/insight_reviews.yaml && git commit
```

`push` migrates the other way (hand-edits, or sign-offs made before D50);
`diff` changes nothing and shows what differs.

---

## Iterate on the console without deploying

The console is the only component you change often, and since WS13 it is also
the only one worth running locally.

```sh
scripts/run_console.sh          # console alone -> http://localhost:8200
```

**It auto-reloads on Python changes.** `index.html` is read from disk on every
request, so HTML/CSS/JS edits appear on a browser refresh; `src/**.py` is
imported once, so without `--reload` an edited endpoint keeps serving the old
code and looks like the change did nothing. That asymmetry cost a debugging
round on a harvest 500 that was only a stale process. `CONSOLE_RELOAD=0`
disables it.

If a change still seems absent: **hard-refresh** (`Cmd+Shift+R`). The console
sends no cache headers on `/`, so a browser may reuse the page it has.

**Why the console alone is a complete environment.** `.env` carries
`A2ALAB_MODE=hosted`, so a locally-running console resolves every target to its
**hosted twin**: Run buttons reach the real Fargate faces and the real AgentCore
runtimes, Observability reads the real Aurora store, the Lab Guide answers from
the repo prose on disk. Nothing else needs to be running on the laptop, and
nothing you do locally touches the deployed console.

**`scripts/run_local.sh` is no longer the default.** It starts sixteen
processes — fourteen protocol faces, the bridge, the console — and fifteen of them
are now hosted. Reach for it when you are changing an **adapter** rather than
the console (`src/platforms/**`, `src/interop/servers/**`, the delegation guard)
and want to exercise it before deploying, because those are the faces' own code
and only that script runs them here.

**Publishing is a separate, deliberate step:**

| You changed | Command |
|---|---|
| `src/console/**` (incl. `index.html`), `config/**`, a Dockerfile | `deploy/console/deploy_console.sh` |
| only an env var or a credential | `deploy/console/deploy_console.sh --skip-build` |

`--skip-build` on a code change ships the **old image** — the env is right and
the code reading it is stale, which reads as a code bug. See the table above.

**Two local-vs-hosted differences worth knowing**, both deliberate:

- **The Harvest button sweeps in-process locally** and fires the Lambda when
  hosted (D54). The local sweep covers four platforms and cannot do ADK at all
  (no GCP key on the laptop's console), so treat a local harvest as a smoke
  test of the UI, not of the harvest. Export `A2ALAB_HARVEST_FUNCTION` before
  the script if you want to exercise the hosted path.
- **Trace writes go to local `traces/` jsonl**, while reads merge local files
  *and* Aurora — so a locally-fired run appears immediately and hosted runs
  appear alongside it. A dev run does not pollute the hosted trace store.

---

## When the console looks broken

Work down this list — it is ordered by how often each was the answer.

1. **Is the domain blocked?** The corporate proxy blocks the lab's whole domain
   at DNS. The tell is a **timeout**, not `NXDOMAIN`. Confirm the record exists
   with `dig +short @1.1.1.1 console-lab.<domain>`, then drop the proxy.
2. **Is it the image?** See
   [above](#deploy-a-code-change-vs-a-config-change).
3. **Empty Observability section?** Postgres is the source of truth (D49). Check
   `A2ALAB_PG_SECRET_ARN` is on the task — the env derivation cannot see
   variables read through a module constant, so it has been missed twice.
4. **`/api/traces` empty but Aurora has rows?** The window is **6 hours**
   (`_REMOTE_WINDOW_S`). An idle overnight lab shows nothing. Not a fault.
5. **Login rejected, or every request 401 after a successful login?** The
   passwords or the JWT private key did not reach the container. Check the
   startup line: `[secret-env] console: loaded N keys from secret: …` and look
   for `A2ALAB_OPERATOR_PASSWORD` and `A2ALAB_JWT_PRIVATE_KEY` by name.

```sh
aws logs tail /ecs/a2alab-console --since 10m --region us-east-1 | grep secret-env
```

---

## Why credentials are read at container start, not per request

**The obvious question:** every `.env` value is already in Secrets Manager
(`a2alab/env/dev`, D39). Why can the console not look a password up there at
login time, so rotating one is `env_sync push` and nothing else?

It could, and something like it is worth doing — but not against that secret,
and the redeploy is not caused by where the value is read.

**Scope is the reason for the per-seam secrets.** `a2alab/env/dev` holds the
whole `.env`: every platform credential, the GCP service-account key, the Aurora
master secret. `a2alab/runtime/console` holds the twelve-odd values the console
actually needs. Pointing the console at the whole-`.env` secret would make a
console compromise a compromise of every credential in the lab — the opposite of
what F1/D39 set out to do. (The console task role currently grants
`secretsmanager:GetSecretValue` on `a2alab/*`, which already includes
`env/dev` — a latent over-grant worth tightening to `a2alab/runtime/console`.)

**The redeploy is not about read timing.** It is there because the per-seam
secret is *built by the deploy script*, from a `keys = [...]` list that lives in
that script. `env_sync push` updates the whole-`.env` secret, which no container
reads. So even a per-request lookup against `a2alab/runtime/console` would still
need something to refresh that secret.

**What would actually remove the redeploy**, in the order that matters:

1. **A rotate script that rewrites the per-seam secrets from `.env`** — no
   image, no task definition, no service update. This is the real fix, and it is
   small. The obstacle is that the key lists live inside four shell scripts;
   they would move to one place both the deploy scripts and the rotate script
   read.
2. **A TTL re-read in `interop/secret_env.py`.** Today `load_secret_env()` runs
   once per process (`_loaded`) with `setdefault`, so a refreshed secret needs a
   restart. A short cache (say 300s) on the values that are checked per request
   would let a rotation take effect on its own.

With (1) alone, rotating becomes `env_sync push` + a rotate command + a service
restart. With (1) and (2), it becomes `env_sync push` + a rotate command. Both
are worth doing; neither was done on 2026-07-28, and the procedure at the top of
this file is the honest one until they are.
