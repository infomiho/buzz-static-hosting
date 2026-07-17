# Automatic Domain Path Transitions

## Goal

Let users add a hostname without choosing infrastructure mode, then move between direct DNS and supported Cloudflare proxying without recreating the claim or replacing its router.

## User Experience

- Adding a domain asks only for the hostname when automatic transitions are available.
- Buzz keeps persistent TXT ownership separate from traffic routing.
- The domain card reports `Waiting for DNS`, `Securing connection`, `Connected`, `Updating connection`, or `Action needed`.
- A Cloudflare indicator describes the observed connection; it is not a permanent user-selected mode.
- Provider details, generation challenges, and component diagnostics remain collapsed unless setup or recovery requires them.
- Buzz never edits customer DNS or Cloudflare settings.

## Authorization Model

An activated claim authorizes one verified hostname to serve one Buzz site through its exact generation-qualified router. Direct and Cloudflare are health policies for that hostname entitlement, not independently enforceable data paths.

Both paths use the same hostname, router, origin certificate, challenge, and application dispatch. Once customer DNS changes, traffic may arrive through the candidate path before Buzz validates and adopts its steady-state health policy. Buzz cannot prevent that traffic without trusted source-path enforcement at Traefik and the application.

Therefore:

- Initial activation requires the complete predicate for the observed path.
- An active transition retains the hostname entitlement while common ownership and identity checks continue.
- Target validation determines when Buzz adopts the new path's continuous health policy.
- The UI says Buzz has retained the hostname entitlement; it does not guarantee public reachability while customer DNS, edge TLS, WAF, or provider configuration is unhealthy.
- Public continuity is an outcome to measure during controlled rollout, not an unconditional product guarantee.

## Superseded Contracts

Plans 08 and 09 required an explicit Cloudflare mode and prohibited DNS from silently changing security policy. This plan supersedes that interaction model only for supported, fully validated transitions:

- DNS observation proposes a candidate path but never completes a policy change.
- Persistent ownership, exact router identity, site identity, origin identity, and the full target predicate remain mandatory.
- There is no fallback from a failing policy. Cutover requires current target evidence and generation-qualified compare-and-set.
- Cloudflare's fail-closed steady-state checks resume after Cloudflare becomes effective.
- Unsupported providers never become effective automatically.

## DNS Observation

Use bounded independent A and AAAA lookups rather than `getaddrinfo`:

- Follow resolver-provided CNAME chains with a bounded depth.
- Distinguish `NoAnswer`, `NXDOMAIN`, timeout, malformed data, and partial-family failure.
- Accept one address family when the other returns an authoritative `NoAnswer`.
- Treat a timeout or invalid response for either configured family as incomplete evidence.
- Reject more than 16 unique addresses.
- Reject non-public addresses before classification.
- Persist a normalized answer-set fingerprint with every observation and probe.

Classify complete answers as:

- `direct`: every address is a configured Buzz ingress address.
- `cloudflare`: every address belongs to the current bundled Cloudflare ranges.
- `mixed`: observations across address families or resolver refreshes contain only ingress and Cloudflare addresses. Cloudflare does not normally return a mixed authoritative answer for same-name records because one proxied A or AAAA causes all same-name A or AAAA records to be treated as proxied.
- `unsupported`: any answer belongs to neither supported path.
- `unavailable`: no complete trustworthy answer exists.

Observation behavior:

- One complete opposite-path or mixed observation starts a candidate handoff immediately so the old validator does not mistake expected propagation for an unsupported steady state.
- Cutover requires two fresh target-only observations separated by at least the greater of 60 seconds and the highest observed DNS TTL, an unchanged answer fingerprint during each probe, and a healthy target predicate.
- Mixed answers can start or continue a handoff but cannot complete one.
- Effective-only answers do not cancel an active handoff before its deadline.
- Unsupported or unavailable observations cannot start or complete a handoff.
- Oscillation updates diagnostics but does not replace the target or mode generation.
- Cloudflare can be a target only when its diagnostics and activation capabilities are enabled.

## State Model

Keep `claim_mode` as the selected health policy for compatibility; it becomes effective only when `activated_at` is non-null. Add a monotonic `mode_generation` to the claim and a one-row transition record containing source mode, target mode, lifecycle state, timestamps, error, and observation evidence.

Transition states:

| State | Serving Result | Meaning |
| --- | --- | --- |
| `observing` | Existing entitlement retained if active | First supported target or mixed observation recorded. |
| `validating` | Existing entitlement retained if active | Stable target evidence is being collected. |
| `action_needed` | Existing entitlement retained until deadline | Target is supported but configuration is unhealthy. |
| `deadline_evaluation` | Existing entitlement retained during atomic evaluation | Buzz revalidates target and effective policies. |
| `completed` | Entitlement retained under target policy | Atomic policy cutover succeeded. |
| `cancelled` | Retained only if effective policy validates | Handoff was cancelled or effective DNS returned at deadline. |
| `failed` | Activation cleared | Neither target nor effective policy validated at deadline, or a common invariant failed. |

Automatic initial onboarding is represented by an unactivated transition with `source_mode=NULL`. The database keeps the compatibility default `claim_mode=direct`, but new clients use `connection_status=securing` rather than presenting that placeholder as effective. The observed target becomes `claim_mode` in the same atomic update that sets `activated_at`. Initial mixed, unsupported, or unavailable DNS remains unactivated and cannot claim a handoff lease.

Lifecycle events and guards:

| Event | Required Guards | Atomic Result |
| --- | --- | --- |
| Start | Verified, routed, supported candidate, no removal | Increment mode generation and create transition. |
| Observe | Current route and mode generations | Update fingerprint, timestamps, and diagnostics. |
| Target healthy | Two stable observations, full target predicate, common invariants | Set effective mode to target and complete transition. |
| Retry | Current transition, not removing | Increment probe generation and collect fresh evidence. |
| Cancel | Current transition and effective policy healthy | Increment mode generation and mark cancelled. |
| Deadline | Current transition and deadline elapsed | Complete target, cancel to healthy effective, or clear activation. |
| Remove | Active claim or transition | Invalidate mode generation, stop serving, then use acknowledged withdrawal. |
| Operator withdrawal | Current route generation | Invalidate transition before route state changes. |

Initial unactivated claims have no entitlement to preserve. Their observation, retry, removal, and stale-probe guards are identical, but cancellation returns to `Waiting for DNS` rather than retaining an effective path.

The transition table keeps only the current or most recent lifecycle. Starting a later handoff increments the claim's mode generation and atomically resets the existing row; structured audit logs retain prior completion, cancellation, and failure events.

## Persistence

Add to `custom_domain_claims`:

- `mode_generation INTEGER NOT NULL DEFAULT 0`.

Add `custom_domain_mode_transitions`:

- `claim_id` primary key and cascading foreign key.
- `mode_generation`.
- `probe_generation`.
- Nullable `source_mode` and required `target_mode`. Source may be null only for unactivated onboarding; otherwise source and target are different supported values.
- Lifecycle `state`.
- `started_at`, `deadline_at`, `checked_at`, and `completed_at`.
- `answer_fingerprint`, `stable_observation_count`, `first_target_observed_at`, `last_target_observed_at`, and `error`.
- `lease_owner` and `lease_expires_at` for external probe ownership.

Database and store invariants:

- A transition row always references the claim's current mode generation.
- Active transition states require a verified, routed, non-removing claim.
- An active claim transition requires non-null activation and deadline; initial onboarding uses the same evidence machinery without claiming continuity.
- An unactivated onboarding transition requires null source mode, null deadline, and non-null target mode.
- Removing, removed, cancelled, expired, or site-detached claims cannot transition.
- Retry, cancellation, replacement, and removal invalidate in-flight probes.
- Target evidence identity includes claim ID, route generation, mode generation, probe generation, and observed mode.
- Rebuild the Cloudflare diagnostic table so target evidence cannot collide across mode generations.
- Migration tests preserve active direct claims, active Cloudflare claims, diagnostic rows, removing claims, and deleted-site tombstones.

## Concurrency

- Reserve a probe only when no unexpired lease exists. Reservation uses an immediate transaction, records the holder and lease expiry, and increments probe generation.
- Only the latest probe generation may write evidence or complete a handoff.
- A crashed holder's lease expires; another instance can reserve the next probe generation and make progress.
- Leases use database time and expire after at most 15 seconds. Deadline evaluation waits for an unexpired lease, then reserves the next probe generation; it never preempts in-flight work.
- Completion, deadline handling, cancellation, and removal use generation-qualified compare-and-set updates.
- Process wall-clock timestamps do not decide evidence ordering.
- Duplicate probes from overlapping server instances are harmless because older reservations cannot write.
- Exact-router health must still be queried from Traefik for the current route generation; no mode transition changes provider snapshots or router acknowledgement.

## Common Health Predicate

Run a common continuous predicate for active direct and Cloudflare claims:

- Persistent TXT ownership.
- Verified site attachment.
- Exact current router rule, service, entrypoint, resolver, and generation.
- Trusted origin TLS.
- Exact origin challenge identity.

Ownership, site identity, router absence or mismatch, and origin identity mismatch clear activation immediately. Runtime API, DNS resolver, and origin transport failures receive the existing bounded transient retry policy.

Direct claims also gain continuous direct-path health checks. Outside transitions, ingress DNS mismatch fails closed after the bounded retry policy. During a supported handoff, only expected effective-path DNS mismatch is replaced by transition evaluation; common checks never pause.

Monitoring service levels:

- Check active claims at least every five minutes, ordered by oldest valid evidence so every claim receives fair scheduling.
- Run at most 20 claim checks concurrently and perform independent DNS, router, edge, and origin operations in parallel under a five-second per-claim budget.
- Treat common-health evidence as stale after ten minutes. A claim that cannot refresh common evidence before expiry loses activation; canonical Buzz hosting remains unaffected.
- `Immediately` means on the first completed check that proves a non-transient invariant failure.
- Capacity tests at the 1,000-claim server limit must demonstrate the evidence-age bound under worst-case timeouts.

## Target Predicates

### Direct

- Complete stable DNS contains only configured ingress addresses.
- Persistent ownership and common health are current.
- Trusted origin TLS returns the exact generation challenge.

### Cloudflare

- Complete stable DNS contains only current bundled Cloudflare ranges.
- Persistent ownership and common health are current.
- Edge TLS validates for the hostname.
- The public generation challenge reaches the exact claim and site without cache, redirect, WAF, Access, Worker, or challenge interference.
- Trusted origin TLS returns the exact generation challenge.
- HTTP-01 forwarding remains an operator release gate proven with a controlled zone rather than inferred from one application probe.

Cloudflare Universal SSL can take 15 minutes to 24 hours after zone activation, and Cloudflare recommends keeping records DNS-only until the edge certificate is active when downtime matters. Without customer Cloudflare API access, Buzz cannot know certificate status before proxying; it reports edge TLS as target-not-ready after observation and does not promise public continuity.

Target probes pin connections to the validated answer set. If the answer fingerprint changes during a probe, its evidence is discarded.

Cutover probes validate every unique resolved address concurrently, including every represented address family. Every address must return healthy edge TLS and the exact public challenge. One healthy address cannot mask stale, denied, redirected, or mismatched behavior on another address. Continuous checks may rotate bounded address subsets only if every current address is covered within the maximum evidence age.

## Failure Taxonomy

Fail immediately on detection:

- TXT ownership missing or mismatched.
- Site attachment or challenge identity mismatch.
- Router authoritatively absent or mismatched.
- Certificate chain, hostname/SAN, expiry, or protocol validation failure.
- Unsupported or non-public DNS in a stable effective policy.

Allow three attempts over at most three minutes:

- DNS resolver timeout or temporary server failure.
- Traefik runtime API unavailable without authoritative router absence.
- TCP timeout, reset, or temporary origin/edge unavailability.
- Cloudflare 525 transport handshake failure.

Redirect, cache, WAF, Access, Worker, managed-challenge, Cloudflare 1014, and Cloudflare 526 responses make a target not ready. If Cloudflare is already effective, they use the existing fail-closed severity; if Cloudflare is only a target, they cannot clear the retained hostname entitlement before the deadline.

## Handoff Deadline

- Active handoffs receive a 24-hour deadline.
- Buzz retains the hostname entitlement while target DNS, edge certificate, and transport configuration converge, but cannot promise that the customer's selected network path is publicly reachable.
- At the deadline Buzz reserves one final probe generation and evaluates both policies:
  - Complete target cutover if the target is healthy.
  - Cancel and retain the effective policy if it is healthy.
  - Clear activation if neither validates.
- Common invariant failure bypasses the deadline and stops serving immediately.

## API Compatibility

- Make creation `mode` optional. Omitted mode uses automatic observation.
- Preserve released explicit semantics: `mode=direct|cloudflare` initializes that effective policy for an unactivated claim, so `buzz-cli@0.10.0` receives the mode and instructions it expects.
- Keep response `mode` as the compatibility-selected policy; it is provisional while `activated_at` is null.
- Add nullable `effective_mode` as the authoritative field for new clients, plus optional `observed_mode`, `target_mode`, `connection_status`, transition timestamps, and transition error.
- Test the actual `buzz-cli@0.10.0` parser and formatter against explicit Cloudflare creation responses.
- Existing transitions consume no additional quota and remain recoverable when new-claim admission is closed.
- Add `POST /sites/{site}/domains/{id}/transition/retry` and `POST /sites/{site}/domains/{id}/transition/cancel`. They are idempotent for the current generation, return `409` for incompatible lifecycle states, and never invoke claim deletion.
- Retrying a terminal failed handoff increments mode and probe generations and starts fresh unactivated validation against the last target. It cannot restore serving until the full target predicate succeeds.
- Keep `DELETE /sites/{site}/domains/{id}` exclusively for destructive claim removal and acknowledged router withdrawal.

## Dashboard And CLI

- When automatic transition admission is enabled, remove the mode selector from domain creation.
- When it is disabled, retain the explicit capability-driven selector so Cloudflare onboarding remains possible.
- Lead with connection status and the required next DNS action.
- During a handoff, distinguish `Buzz retained this hostname` from public reachability and report target-path failures truthfully.
- Keep component diagnostics and verification URLs under advanced disclosure.
- New CLI versions stop promoting `--mode` but continue accepting it.
- Format effective, observed, and target paths separately.
- Expose retry and cancel only for active or failed handoffs.

## Operator Controls

- Add `BUZZ_AUTOMATIC_DOMAIN_TRANSITION_ADMISSION_ENABLED`, default `false`.
- Closing admission prevents new automatic handoffs but the coordinator always drains active handoffs and enforces deadlines.
- A bearer-protected private control-listener endpoint lists active handoffs, deadlines, effective paths, and public-health evidence. It is not mounted on the public FastAPI application and uses a dedicated operator token rather than ordinary user sessions or deployment tokens.
- Configure that endpoint with `BUZZ_CUSTOM_DOMAIN_OPERATOR_TOKEN`. Support overlap during token rotation, emit audit events for reads and transition actions without logging tokens, and never reuse the Traefik provider token.
- Emergency cancellation validates the effective policy before retaining activation.
- Existing stable claims continue their common and mode-specific health checks while admission is closed.
- Cloudflare remains an optional target governed by its existing diagnostics and activation flags.
- Canonical Buzz hosting remains independent from every transition component.

## Verification

- Fresh direct onboarding without a mode selector.
- Fresh onboarding when DNS is already Cloudflare-proxied.
- Released `buzz-cli@0.10.0` explicit direct and Cloudflare onboarding.
- Active direct to Cloudflare with direct-only, mixed, then Cloudflare-only answers.
- Active Cloudflare to direct with Cloudflare-only, mixed, then ingress-only answers.
- Requests arriving through the candidate path before policy cutover follow the documented hostname-entitlement behavior.
- Split A/AAAA paths, one-family timeout, CNAME chains, excessive answers, alternating answers, answer-set changes during probes, and return to effective DNS.
- Cutover fails when any resolved address has invalid TLS, stale content, denial, redirect, or challenge mismatch while another address is healthy.
- Target TLS pending, Cloudflare 525/526, WAF denial, stale cache, DNS outage, unsupported addresses, and loss of public reachability before target readiness.
- Active direct ownership loss, DNS mismatch, router mismatch, origin mismatch, restart, and recovery.
- Transition cancellation and restart with stale probes from the prior mode generation.
- Deadline behavior when target, effective, both, or neither policy validates.
- User removal, operator withdrawal, and site deletion races in both directions.
- Buzz and Traefik restarts during each lifecycle state.
- Two database connections interleave probe reservation, completion, cancellation, deadline, and removal; only the current generations mutate state.
- Two coordinators cannot starve each other: an unexpired lease prevents superseding work, and lease expiry permits recovery after a crashed holder.
- At 1,000 active claims with worst-case timeouts, every claim remains within the maximum common-evidence age.
- Existing direct and Cloudflare claims outside handoffs retain documented fail-closed behavior.
- Canonical health, deployment, and site URLs remain unaffected.

## Controlled Rollout

1. Deploy schema, common health checks, observation, and read-only status with automatic admission disabled.
2. Compare observations against existing direct and Cloudflare claims without starting handoffs.
3. Enable admission for a controlled hostname and exercise both directions, mixed DNS, target failure, cancellation, deadline, and restart.
4. Compare edge and origin content hashes while separately recording public reachability.
5. Close admission, verify active handoffs drain, and test emergency cancellation.
6. Enable automatic admission for users and hide the creation selector only while the capability is ready.

## Documentation Validation

Validated against current Cloudflare documentation on 2026-07-17:

- [Proxy status](https://developers.cloudflare.com/dns/proxy-status/): proxied A, AAAA, and CNAME records return Cloudflare anycast addresses; DNS-only records return origin addresses; TXT records are always DNS-only; proxied TTL is normally 300 seconds; one proxied same-name A or AAAA makes all same-name records proxied; and a proxied CNAME anywhere in a chain can proxy the hostname.
- [Enable Universal SSL](https://developers.cloudflare.com/ssl/edge-certificates/universal-ssl/enable-universal-ssl/): full-zone Universal SSL provisioning can take 15 minutes to 24 hours, Cloudflare recommends remaining DNS-only until the certificate is active when minimizing downtime, and partial setups may require explicit DCV records.
- [Full (strict)](https://developers.cloudflare.com/ssl/origin-configuration/ssl-modes/full-strict/): the origin must accept HTTPS on port 443 and present an unexpired hostname-matching certificate from a public CA or Cloudflare Origin CA, otherwise visitors can receive 526.
- [DCV troubleshooting](https://developers.cloudflare.com/ssl/edge-certificates/changing-dcv-method/troubleshooting/): Workers matching broad routes can intercept `/.well-known/acme-challenge/*` and other validation paths, so target validation must continue detecting path interference.

Buzz intentionally requires its publicly trusted origin certificate even though Cloudflare Full (strict) can also trust Cloudflare Origin CA. This preserves direct-path compatibility and independent origin validation.

## Acceptance Criteria

- Users add a hostname without selecting infrastructure mode when automatic transitions are available.
- Supported DNS changes do not recreate the claim, rotate its router, or clear its hostname entitlement solely because target validation is pending.
- UI copy does not promise public uptime that Buzz cannot control.
- Cutover is atomic and guarded by current route, mode, and probe generations.
- Mixed DNS during a bounded handoff does not cause Buzz to withdraw an otherwise valid entitlement.
- Common ownership and identity failures remain fail-closed.
- Unsupported providers never trigger implicit fallback or cutover.
- Existing released CLI behavior remains correct.
- Both directions pass controlled-zone verification with canonical hosting continuously healthy.

## Rollback

Close automatic transition admission. Continue coordinator execution until every active handoff completes, cancels to a validated effective policy, or reaches its deadline. Keep the explicit creation selector available while admission is closed. No router or DNS rollback is performed by the feature flag.

## Dependencies

- Plan 09, with the explicit-mode interaction contract superseded as described above.
