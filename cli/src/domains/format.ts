import type {
  DiagnosticComponent,
  DomainClaim,
  DomainConnectionStatus,
} from "./schema.js";

function ownership(claim: DomainClaim): string {
  if (claim.status === "pending" && claim.last_error === "txt_mismatch") {
    return "Pending: expected TXT value not found";
  }
  if (claim.status === "pending" && claim.last_error === "dns_unavailable") {
    return "Pending: TXT lookup unavailable";
  }
  if (claim.status === "pending") return `Pending, expires ${claim.expires_at}`;
  return claim.status[0].toUpperCase() + claim.status.slice(1);
}

function routerStatus(claim: DomainClaim): string {
  const errors: Record<string, string> = {
    runtime_api_unavailable: "Traefik runtime status unavailable; retrying",
    router_not_observed: "Router not yet observed in Traefik",
    router_configuration_mismatch: "Router configuration mismatch; retrying",
    withdrawal_snapshot_not_acknowledged: "Waiting for Traefik to poll withdrawal",
    router_still_present: "Router still present; retrying",
  };
  if (claim.route_error) return errors[claim.route_error] ?? `Error: ${claim.route_error}`;
  return {
    not_routed: "Not published",
    publishing: "Publishing, waiting for Traefik acknowledgement",
    routed: "Acknowledged",
    removing: "Removing, waiting for withdrawal acknowledgement",
    removed: "Withdrawn",
  }[claim.route_status];
}

function routingDns(claim: DomainClaim): string {
  if (isActive(claim)) return "Valid";
  const errors: Record<string, string> = {
    dns_no_addresses: "No A or AAAA addresses found",
    dns_unexpected_address: "Does not resolve only to this Buzz server",
    dns_unavailable: "Temporarily unavailable; retrying",
  };
  if (claim.activation_error && errors[claim.activation_error]) {
    return errors[claim.activation_error];
  }
  if (
    claim.activation_error &&
    ["tls_invalid", "origin_unavailable", "challenge_mismatch"].includes(
      claim.activation_error
    )
  ) {
    return "Valid";
  }
  if (claim.activation_error === "activation_check_failed") {
    return "Validation state unknown; retrying";
  }
  return claim.route_status === "routed" ? "Checking" : "Not checked";
}

function originTls(claim: DomainClaim): string {
  if (isActive(claim)) return "Trusted";
  const errors: Record<string, string> = {
    tls_invalid: "Waiting for a trusted certificate",
    origin_unavailable: "Origin unavailable; retrying",
    challenge_mismatch: "Origin challenge mismatch; retrying",
    activation_check_failed: "Validation failed unexpectedly; retrying",
  };
  if (claim.activation_error && errors[claim.activation_error]) {
    return errors[claim.activation_error];
  }
  if (
    claim.activation_error &&
    ["dns_no_addresses", "dns_unexpected_address", "dns_unavailable"].includes(
      claim.activation_error
    )
  ) {
    return "Not checked";
  }
  return claim.route_status === "routed" ? "Checking" : "Not checked";
}

export function formatDomainClaim(claim: DomainClaim): string {
  if (claim.mode === "cloudflare") return formatCloudflareClaim(claim);
  const active = isActive(claim);
  const removal =
    claim.route_status === "removing" && claim.removal_requested_at
      ? "In progress"
      : claim.status === "cancelled" || claim.route_status === "removed"
        ? "Complete"
        : claim.route_status === "removing"
          ? "Not requested (router withdrawal in progress)"
        : "Not requested";
  const lines = [
    claim.hostname,
    ...connectionLines(claim),
    `  Site:        ${claim.site_name ?? "Deleted site"}`,
    `  Ownership:   ${ownership(claim)}`,
    `  Routing DNS: ${routingDns(claim)}`,
    `  Router:      ${routerStatus(claim)}`,
    `  Origin TLS:  ${originTls(claim)}`,
    `  Public TLS:  ${publicTlsStatus(claim, active)}`,
    `  Removal:     ${removal}`,
  ];
  if (claim.status === "pending") {
    lines.push(
      "  Ownership record:",
      `    Type:  ${claim.verification.type}`,
      `    Name:  ${claim.verification.name}`,
      `    Value: ${claim.verification.value}`
    );
  }
  if (claim.challenge_path && !claim.activated_at) {
    lines.push(
      `  Public challenge: ${claim.challenge_seen_at ? "Observed" : "Waiting"}`,
      `    https://${claim.hostname}${claim.challenge_path}`
    );
  }
  return lines.join("\n");
}

function connectionLines(claim: DomainClaim): string[] {
  const labels: Record<DomainConnectionStatus, string> = {
    waiting_for_dns: "Waiting for DNS",
    securing: "Securing connection",
    connected: "Connected",
    updating: "Updating connection",
    action_needed: "Action needed",
  };
  if (!claim.connection_status) return [];
  const lines = [`  Connection:  ${labels[claim.connection_status]}`];
  if (claim.effective_mode !== undefined) {
    lines.push(`  Effective:   ${pathName(claim.effective_mode)}`);
  }
  if (claim.observed_mode) lines.push(`  Observed:    ${pathName(claim.observed_mode)}`);
  if (claim.target_mode) lines.push(`  Target:      ${pathName(claim.target_mode)}`);
  if (claim.transition_error) lines.push(`  Transition:  ${claim.transition_error}`);
  return lines;
}

function pathName(mode: DomainClaim["observed_mode"]): string {
  if (!mode) return "Not connected";
  if (mode === "cloudflare") return "Cloudflare proxy";
  return mode[0].toUpperCase() + mode.slice(1);
}

function diagnosticValue(component: DiagnosticComponent | undefined): string {
  if (!component) return "Waiting for router acknowledgement";
  return component.error ?? component.status;
}

function formatCloudflareClaim(claim: DomainClaim): string {
  const diagnostic = claim.cloudflare_diagnostics;
  const removal =
    claim.route_status === "removing" && claim.removal_requested_at
      ? "In progress"
      : claim.status === "cancelled" || claim.route_status === "removed"
        ? "Complete"
        : "Not requested";
  const lines = [
    claim.hostname,
    ...connectionLines(claim),
    `  Site:            ${claim.site_name ?? "Deleted site"}`,
    "  Mode:            Cloudflare proxy",
    `  Ownership:       ${claim.status === "pending" ? ownership(claim) : diagnosticValue(diagnostic?.ownership)}`,
    `  Router:          ${routerStatus(claim)}`,
  ];
  if (diagnostic) {
    lines.push(
      `  Cloudflare DNS:  ${diagnosticValue(diagnostic.dns)}`,
      `  Edge TLS:        ${diagnosticValue(diagnostic.edge_tls)}`,
      `  Edge challenge:  ${diagnosticValue(diagnostic.edge_http)}`,
      `  HTTP forwarding: ${diagnosticValue(diagnostic.http_forwarding)}`,
      `  Origin:          ${diagnosticValue(diagnostic.origin)}`
    );
  }
  lines.push(
    `  Activation:      ${cloudflareActivation(claim)}`,
    `  Removal:         ${removal}`
  );
  if (claim.status === "pending") {
    lines.push(
      "  Ownership record:",
      `    Type:  ${claim.verification.type}`,
      `    Name:  ${claim.verification.name}`,
      `    Value: ${claim.verification.value}`
    );
  }
  if (claim.challenge_path) {
    lines.push(
      `  Public challenge: ${claim.challenge_seen_at ? "Observed" : "Waiting"}`,
      `    https://${claim.hostname}${claim.challenge_path}`
    );
  }
  return lines.join("\n");
}

function cloudflareActivation(claim: DomainClaim): string {
  const projected = projectedActivation(claim);
  if (projected) return projected;
  if (isActive(claim) && claim.activation_error) {
    return `Degraded (${claim.cloudflare_diagnostics?.consecutive_failures ?? 1}/3): ${claim.activation_error}`;
  }
  if (isActive(claim)) return "Active";
  return claim.activation_error ?? "Not active";
}

function isActive(claim: DomainClaim): boolean {
  if (claim.connection_status !== undefined) {
    return claim.connection_status === "connected";
  }
  return (
    claim.status === "verified" &&
    claim.route_status === "routed" &&
    claim.activated_at !== null
  );
}

function publicTlsStatus(claim: DomainClaim, legacyActive: boolean): string {
  return projectedActivation(claim) ?? (legacyActive ? "Active" : "Not active");
}

function projectedActivation(claim: DomainClaim): string | null {
  if (claim.connection_status === undefined) return null;
  if (claim.connection_status === "connected") return "Active";
  if (claim.connection_status === "updating") return "Authorization retained";
  return "Not active";
}
