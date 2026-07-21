export type DomainStatus = "pending" | "verified" | "expired" | "cancelled";
export type DomainRouteStatus =
  | "not_routed"
  | "publishing"
  | "routed"
  | "removing"
  | "removed";
export type DomainMode = "direct" | "cloudflare";
export type DomainConnectionStatus =
  | "waiting_for_dns"
  | "securing"
  | "connected"
  | "updating"
  | "action_needed";

export interface DomainClaim {
  id: number;
  hostname: string;
  site_name: string | null;
  status: DomainStatus;
  verification: { type: "TXT"; name: string; value: string };
  created_at: string;
  expires_at: string;
  verified_at: string | null;
  last_checked_at: string | null;
  last_error: string | null;
  route_status: DomainRouteStatus;
  route_generation: number;
  route_error: string | null;
  challenge_path: string | null;
  challenge_seen_at: string | null;
  activated_at: string | null;
  activation_checked_at: string | null;
  activation_error: string | null;
  removal_requested_at: string | null;
  withdrawn_at: string | null;
  mode: DomainMode;
  effective_mode?: DomainMode | null;
  observed_mode?: DomainMode | "mixed" | "unsupported" | "unavailable" | null;
  target_mode?: DomainMode | null;
  connection_status?: DomainConnectionStatus;
  transition_started_at?: string | null;
  transition_deadline_at?: string | null;
  transition_error?: string | null;
  cloudflare_diagnostics: CloudflareDiagnostic | null;
}

export interface DiagnosticComponent {
  status: string;
  error: string | null;
}

export interface CloudflareDiagnostic {
  generation: number;
  checked_at: string;
  ranges_version: string | null;
  ownership: DiagnosticComponent;
  dns: DiagnosticComponent;
  edge_tls: DiagnosticComponent;
  edge_http: DiagnosticComponent & {
    status_code: number | null;
    address: string | null;
    cf_ray: string | null;
    cf_cache_status: string | null;
    redirect_location: string | null;
  };
  http_forwarding: DiagnosticComponent & { status_code: number | null };
  origin: DiagnosticComponent;
  consecutive_failures: number;
}

export interface DomainCapability {
  status: "disabled" | "unready" | "ready";
  detail: string | null;
  enabled: boolean;
  control_ready: boolean;
  routing_enabled: boolean;
  routing_targets: { type: "A" | "AAAA"; value: string }[];
  automatic?: {
    ready: boolean;
    detail: string | null;
  };
  cloudflare: {
    supported: boolean;
    detail: string | null;
  };
}

export function isCapability(value: unknown): value is DomainCapability {
  if (!value || typeof value !== "object") return false;
  const capability = value as Partial<DomainCapability>;
  return (
    ["disabled", "unready", "ready"].includes(capability.status ?? "") &&
    typeof capability.enabled === "boolean" &&
    typeof capability.control_ready === "boolean" &&
    typeof capability.routing_enabled === "boolean" &&
    (capability.detail === null || typeof capability.detail === "string") &&
    Array.isArray(capability.routing_targets) &&
    capability.routing_targets.every(
      (target) =>
        target &&
        ["A", "AAAA"].includes(target.type) &&
        typeof target.value === "string"
    ) &&
    (!capability.automatic ||
      (typeof capability.automatic.ready === "boolean" &&
        (capability.automatic.detail === null ||
          typeof capability.automatic.detail === "string"))) &&
    (!capability.cloudflare ||
      (typeof capability.cloudflare.supported === "boolean" &&
        (capability.cloudflare.detail === null ||
          typeof capability.cloudflare.detail === "string")))
  );
}

function isDiagnosticComponent(value: unknown): value is DiagnosticComponent {
  if (!value || typeof value !== "object") return false;
  const component = value as Partial<DiagnosticComponent>;
  return (
    typeof component.status === "string" &&
    (component.error === null || typeof component.error === "string")
  );
}

function isCloudflareDiagnostic(value: unknown): value is CloudflareDiagnostic {
  if (!value || typeof value !== "object") return false;
  const diagnostic = value as Partial<CloudflareDiagnostic>;
  const nullableString = (field: unknown) => field === null || typeof field === "string";
  return (
    Number.isInteger(diagnostic.generation) &&
    typeof diagnostic.checked_at === "string" &&
    nullableString(diagnostic.ranges_version) &&
    isDiagnosticComponent(diagnostic.ownership) &&
    isDiagnosticComponent(diagnostic.dns) &&
    isDiagnosticComponent(diagnostic.edge_tls) &&
    isDiagnosticComponent(diagnostic.edge_http) &&
    (diagnostic.edge_http?.status_code === null ||
      Number.isInteger(diagnostic.edge_http?.status_code)) &&
    nullableString(diagnostic.edge_http?.address) &&
    nullableString(diagnostic.edge_http?.cf_ray) &&
    nullableString(diagnostic.edge_http?.cf_cache_status) &&
    nullableString(diagnostic.edge_http?.redirect_location) &&
    isDiagnosticComponent(diagnostic.http_forwarding) &&
    (diagnostic.http_forwarding?.status_code === null ||
      Number.isInteger(diagnostic.http_forwarding?.status_code)) &&
    isDiagnosticComponent(diagnostic.origin) &&
    Number.isInteger(diagnostic.consecutive_failures)
  );
}

export function isClaim(value: unknown): value is DomainClaim {
  if (!value || typeof value !== "object") return false;
  const claim = value as Partial<DomainClaim>;
  const nullableString = (field: unknown) => field === null || typeof field === "string";
  return (
    Number.isInteger(claim.id) &&
    typeof claim.hostname === "string" &&
    nullableString(claim.site_name) &&
    ["pending", "verified", "expired", "cancelled"].includes(claim.status ?? "") &&
    ["not_routed", "publishing", "routed", "removing", "removed"].includes(
      claim.route_status ?? ""
    ) &&
    !!claim.verification &&
    claim.verification.type === "TXT" &&
    typeof claim.verification.name === "string" &&
    typeof claim.verification.value === "string" &&
    typeof claim.created_at === "string" &&
    typeof claim.expires_at === "string" &&
    nullableString(claim.verified_at) &&
    nullableString(claim.last_checked_at) &&
    nullableString(claim.last_error) &&
    Number.isInteger(claim.route_generation) &&
    nullableString(claim.route_error) &&
    nullableString(claim.challenge_path) &&
    nullableString(claim.challenge_seen_at) &&
    nullableString(claim.activated_at) &&
    nullableString(claim.activation_checked_at) &&
    nullableString(claim.activation_error) &&
    nullableString(claim.removal_requested_at) &&
    nullableString(claim.withdrawn_at) &&
    ["direct", "cloudflare"].includes(claim.mode ?? "") &&
    (claim.effective_mode === undefined ||
      claim.effective_mode === null ||
      ["direct", "cloudflare"].includes(claim.effective_mode)) &&
    (claim.observed_mode === undefined ||
      claim.observed_mode === null ||
      ["direct", "cloudflare", "mixed", "unsupported", "unavailable"].includes(
        claim.observed_mode
      )) &&
    (claim.target_mode === undefined ||
      claim.target_mode === null ||
      ["direct", "cloudflare"].includes(claim.target_mode)) &&
    (claim.connection_status === undefined ||
      ["waiting_for_dns", "securing", "connected", "updating", "action_needed"].includes(
        claim.connection_status
      )) &&
    [
      claim.transition_started_at,
      claim.transition_deadline_at,
      claim.transition_error,
    ].every((field) => field === undefined || nullableString(field)) &&
    (claim.cloudflare_diagnostics === null ||
      isCloudflareDiagnostic(claim.cloudflare_diagnostics))
  );
}

export function isClaimArray(value: unknown): value is DomainClaim[] {
  return Array.isArray(value) && value.every(isClaim);
}

export function normalizeClaim(value: unknown): unknown {
  if (!value || typeof value !== "object") return value;
  const claim = value as Partial<DomainClaim>;
  const diagnostic = claim.cloudflare_diagnostics;
  return {
    ...claim,
    mode: claim.mode ?? "direct",
    cloudflare_diagnostics: diagnostic
      ? {
          ...diagnostic,
          ownership: diagnostic.ownership ?? { status: "not_checked", error: null },
          consecutive_failures: diagnostic.consecutive_failures ?? 0,
        }
      : null,
  };
}

export function normalizeClaimArray(value: unknown): unknown {
  return Array.isArray(value) ? value.map(normalizeClaim) : value;
}

export function normalizeCapability(value: unknown): unknown {
  if (!value || typeof value !== "object") return value;
  const capability = value as Partial<DomainCapability>;
  return {
    ...capability,
    cloudflare: capability.cloudflare ?? {
      supported: false,
      detail: "This Buzz server does not support Cloudflare proxy diagnostics",
    },
  };
}
