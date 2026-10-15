# 19 — Getting `agenticthings.com` past the corporate Zscaler

Status: **resolved 2026-10-14.** The Site Review request filed 2026-10-13
(`agenticthings.com`, `console-lab`, `faces-lab`; suggested categories *General AI
and ML Applications* + *Other Information Technology*) was accepted: the operator
confirmed the category was set, and a re-probe from the laptop with the Zscaler
client connected reached all three hosts (`console-lab` and `faces-lab` 200,
`bridge-lab` 404 on `/`, its normal answer there), every one served `Server:
cloudflare`, not a Zscaler page. No second policy block on *General AI and ML
Applications* showed up. One correction to step 3 below: DNS answered with the
public Cloudflare addresses, not `100.64.0.x`. The success test is the `Server`
header, not the resolved address. Step 4 (escalation) wasn't needed. The ALB
`Host`-header route stays documented for testing DNS or Cloudflare themselves
(step 5).

## The problem

On the operator's corporate laptop, every `*.agenticthings.com` host (console,
faces, bridge) is unreachable while the Zscaler client is connected. It shows up
as "the domain doesn't resolve", which is why it has been worked around (hit the
ALB with a `Host` header) rather than fixed.

## Diagnosis (2026-10-13)

| Probe | Result |
|---|---|
| Zscaler tunnel DNS (`100.64.0.x`) | **times out** for the apex and every subdomain; `example.com`, `anthropic.com` resolve normally |
| Public DNS (`8.8.8.8`, `1.1.1.1`) | resolves — Cloudflare nameservers, subdomains proxied to Cloudflare edge IPs |
| HTTP to a pinned Cloudflare IP | `403`, `Server: Zscaler/6.2`, block page |
| HTTPS to a pinned Cloudflare IP | connection reset by Zscaler |

The block page states the cause:

```
Not allowed to browse Miscellaneous or Unknown category
reasoncode=CATEGORY_DENIED   cat=Miscellaneous or Unknown
```

So this is a **category** block, not a threat listing. Zscaler's global URL
database has no category for the domain, and the corporate tenant policy denies
the "Miscellaneous or Unknown" bucket. The page separately mentions the
security team's own custom lists ("CSIRT-Block-List", "Egress-Block-List") —
the domain is **not** on those.

Probable reason it is uncategorized: there is nothing for a crawler to classify.
The apex `A` records point at a registrar forwarding/parking service, not at the
lab, and every subdomain is the auth-gated console or a JSON API behind
Cloudflare. `www` follows the apex to the same forwarding service.

### Who owns which part

- **The category** is Zscaler's. `sitereview.zscaler.com` is Zscaler's own
  public recategorization service (Zscaler-owned address space), shared across
  all Zscaler customers — it is not a company-specific tool. A successful
  recategorization fixes the domain for every Zscaler tenant.
- **The policy** ("deny Miscellaneous or Unknown") is the corporate tenant's,
  administered by the internal security / TechForce team. Only they can grant a
  per-domain exception or remove a custom-list entry.

### Blast radius

Only the operator's browser and local scripts are affected. Nothing in the lab
itself traverses Zscaler: Agentforce's Apex callouts to `bridge-lab` leave from
Salesforce infrastructure, and the platform-to-platform hops (AWS, GCP, Azure,
Heroku) never touch the laptop. Path A and every hosted cell keep working; what
breaks is opening the console and smoke-testing it from this machine.

## Plan

1. **Give the apex real content.** Point `agenticthings.com` at a public, static
   landing page (a short description of the lab — what it is, that it's an
   engineering demo — with no auth). This is what the reviewer and crawler will
   look at. Update `plan/09-deployment-map.md` if it becomes a new hosted
   component.
2. **Submit to Zscaler Site Review.** Use the "Click here" link on the block page
   (it pre-fills the URL and the denying rule) or `sitereview.zscaler.com`
   directly. Submit the apex `agenticthings.com` so subdomains inherit, and name
   the live hosts in the description in case they are reviewed separately:
   `console-lab` (the console, browser-facing), `faces-lab` (hosted agent faces),
   `bridge-lab` (Path A bridge), and the cloudflared tunnel hosts
   `claude-rest-lab` / `claude-mcp-lab` / `claude-a2a-lab` (local dev stack,
   `deploy/tunnel/config.yml`). `lab.agenticthings.com` does not exist (the
   abandoned delegated-zone design, plan/04-runbooks.md). Suggested
   category: *Professional Services* or *Business* (fallback *Web Hosting*).
   Description: "Personal engineering lab / demo site for agent-to-agent
   interoperability across Salesforce Agentforce, AWS, Google Cloud and Azure."
3. **Wait and re-probe.** Zscaler reviews usually land in a few business days.
   Re-run the probe below; success is DNS resolving through `100.64.0.x` and the
   console returning its own response instead of `Server: Zscaler`.
4. **Escalate only if step 2 is rejected or stalls.** Open a ticket via the
   internal TechForce "Zscaler web access block and recategorization" process
   (linked from the block page) or ask in the help-techforce channel, noting
   `reasoncode=CATEGORY_DENIED` (not a custom block list) and the business
   purpose.
5. **Close out.** Mark this doc resolved, and retire the ALB `Host`-header
   workaround from day-to-day use (keep it documented — it is still useful when
   DNS or Cloudflare is the thing under test).

**Watch for a second block.** The submission suggested *General AI and ML
Applications*. Corporate policies often restrict that category separately (it is
the generative-AI bucket). If the re-probe shows a Zscaler page again but with
`cat=General AI and ML Applications`, recategorization worked and the remaining
block is policy — go straight to step 4 and ask for a domain exception.

### Interim

Keep using the documented route for hosted smoke tests: connect to the ALB at
`$A2ALAB_BRIDGE_URL` with the `Host` header set to the console or faces
hostname, `follow_redirects=False`. Do not disable or tunnel around the Zscaler
client — that is a policy violation, and the recategorization is the fix.

### Probe

```sh
dig +short +time=3 +tries=1 console-lab.agenticthings.com     # via Zscaler DNS
dig +short console-lab.agenticthings.com @1.1.1.1             # public, for the IP
curl -sS -i -m 10 --resolve console-lab.agenticthings.com:80:<ip> \
  http://console-lab.agenticthings.com/ | head -5             # Server: Zscaler = still blocked
```
