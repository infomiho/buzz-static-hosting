# Buzz

Self-hosted static site hosting. Sites deploy to subdomains of the Buzz domain; a site may additionally claim custom hostnames. This file records the project's domain language.

## Language

### Custom domains

**Claim**:
One site's ownership assertion over one hostname, verified through a DNS TXT record and tracked through its whole life (pending, verified, routed, activated, withdrawn).
_Avoid_: domain record, custom domain entry

**Claim mode**:
How traffic for a claim reaches the origin: `direct` (DNS points at Buzz ingress) or `cloudflare` (proxied through Cloudflare).
_Avoid_: proxy mode, connection type

**Admission**:
Whether the server currently accepts new claims or transitions, decided by operator flags plus runtime health. Distinct from serving: already-routed claims keep serving even when admission is off.
_Avoid_: enablement, gating

**Activation**:
The fail-closed verdict that a routed claim's hostname really serves this Buzz origin. Only activated claims serve traffic.
_Avoid_: go-live, verification (that word is reserved for TXT ownership checks)

**Onboarding**:
A transition with no source mode: a claim's first path to activation in its target mode.

**Handoff**:
An active transition between modes for an already-activated claim (for example Cloudflare to direct), generation-fenced and deadline-bound.
_Avoid_: migration, switchover

**Withdrawal**:
Acknowledged removal of a claim's Traefik router. Required before custom-domain infrastructure may be disabled.

**Evidence**:
Recorded DNS, edge, and origin observations that back activation and transition decisions.
_Avoid_: probe results (a probe is the act; evidence is the recorded outcome)

**Diagnostics**:
Explicit Cloudflare path checks (DNS ranges, edge TLS/HTTP, forwarding, origin identity, ownership) recorded per claim and generation.

**Capabilities**:
The projection of operator flags plus runtime health into ready/unready statuses with operator-facing detail, per feature (control, routing, Cloudflare, automatic transitions).
_Avoid_: feature flags (flags are inputs; capabilities are the derived verdict)

**Connection**:
The derived, user-facing description of how a claim's hostname currently connects (status label, path, retry/cancel affordances).

**Task**:
The single next action a claim's owner should take, derived from claim plus connection.

**Claim view**:
The one read interface over a claim: claim, connection, task, diagnostics, and transition in a single result. Callers render claim views; they do not join stores.
_Avoid_: domain response, claim details

**Runtime**:
The custom-domains package's lifecycle owner: wiring order, control server, reconcile loop, startup guards, capabilities, and request-time lookups behind one object.
_Avoid_: app state, wiring

## Example dialogue

Dev: "The dashboard shows a verified claim but no router."
Domain expert: "Then admission was on when the claim was created, but look at the capabilities verdict: routing is probably unready, so the reconciler will not publish. Once it routes, activation still has to pass before it serves."
Dev: "And if the owner moves DNS to Cloudflare?"
Domain expert: "That is a handoff. The coordinator collects evidence each pass and either completes it by the deadline or fails closed. The claim view will surface it as a task, so the dashboard needs no extra joins."
