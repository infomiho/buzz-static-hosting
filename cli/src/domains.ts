import { domainToASCII } from "node:url";
import {
  apiRequest,
  CliError,
  errorMessage,
  type CliOptions,
  type Site,
} from "./lib.js";

export type DomainStatus = "pending" | "verified" | "expired" | "cancelled";
export type DomainRouteStatus =
  | "not_routed"
  | "publishing"
  | "routed"
  | "removing"
  | "removed";

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
}

export interface DomainCapability {
  status: "disabled" | "unready" | "ready";
  detail: string | null;
  enabled: boolean;
  control_ready: boolean;
  admission_enabled: boolean;
  routing_enabled: boolean;
  routing_targets: { type: "A" | "AAAA"; value: string }[];
}

async function domainRequest(
  path: string,
  options: RequestInit = {},
  cliOptions: CliOptions = {}
): Promise<Response> {
  try {
    return await apiRequest(path, options, { cliOptions });
  } catch (error) {
    if (
      error instanceof CliError &&
      error.message === "Deploy tokens cannot perform this operation"
    ) {
      throw new CliError(
        "Deployment tokens cannot manage custom domains",
        "Run 'buzz login' and retry with a full session"
      );
    }
    throw error;
  }
}

function isCapability(value: unknown): value is DomainCapability {
  if (!value || typeof value !== "object") return false;
  const capability = value as Partial<DomainCapability>;
  return (
    ["disabled", "unready", "ready"].includes(capability.status ?? "") &&
    typeof capability.enabled === "boolean" &&
    typeof capability.control_ready === "boolean" &&
    typeof capability.admission_enabled === "boolean" &&
    typeof capability.routing_enabled === "boolean" &&
    (capability.detail === null || typeof capability.detail === "string") &&
    Array.isArray(capability.routing_targets) &&
    capability.routing_targets.every(
      (target) =>
        target &&
        ["A", "AAAA"].includes(target.type) &&
        typeof target.value === "string"
    )
  );
}

function isClaim(value: unknown): value is DomainClaim {
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
    nullableString(claim.withdrawn_at)
  );
}

export async function getDomainCapability(
  cliOptions: CliOptions = {}
): Promise<DomainCapability> {
  const response = await domainRequest("/capabilities/custom-domains", {}, cliOptions);
  if (response.status === 404) {
    throw new CliError(
      "This Buzz server does not expose custom-domain capability information",
      "Update the server before using CLI domain management"
    );
  }
  if (!response.ok) {
    throw new CliError(await errorMessage(response, "Could not check custom-domain capability"));
  }
  const capability: unknown = await response.json();
  if (!isCapability(capability)) {
    throw new CliError("Server returned an invalid custom-domain capability response");
  }
  return capability;
}

export async function getDomainClaims(
  siteName: string,
  cliOptions: CliOptions = {}
): Promise<DomainClaim[]> {
  const response = await domainRequest(
    `/sites/${encodeURIComponent(siteName)}/domains`,
    {},
    cliOptions
  );
  if (response.status === 404) throw new CliError(`Site '${siteName}' not found`);
  if (!response.ok) {
    throw new CliError(await errorMessage(response, "Could not list custom domains"));
  }
  const claims: unknown = await response.json();
  if (!Array.isArray(claims) || !claims.every(isClaim)) {
    throw new CliError("Server returned an invalid custom-domain response");
  }
  return claims;
}

export async function getAllDomainClaims(
  cliOptions: CliOptions = {}
): Promise<DomainClaim[]> {
  const response = await domainRequest("/sites", {}, cliOptions);
  if (!response.ok) {
    throw new CliError(await errorMessage(response, "Could not list sites"));
  }
  const data: unknown = await response.json();
  if (
    !Array.isArray(data) ||
    !data.every(
      (site) => site && typeof site === "object" && typeof site.name === "string"
    )
  ) {
    throw new CliError("Server returned an invalid site response");
  }
  const sites = data as Site[];
  const claims: DomainClaim[] = [];
  for (const site of sites) {
    claims.push(...(await getDomainClaims(site.name, cliOptions)));
  }
  return claims.sort((a, b) =>
    `${a.site_name ?? ""}\0${a.hostname}`.localeCompare(
      `${b.site_name ?? ""}\0${b.hostname}`
    )
  );
}

export function resolveDomainClaim(
  claims: DomainClaim[],
  rawHostname: string
): DomainClaim {
  const input = rawHostname.trim();
  const hostname = domainToASCII(input).toLowerCase().replace(/\.$/, "");
  const matches = claims.filter((claim) => claim.hostname === hostname);
  const active = matches.filter((claim) =>
    ["pending", "verified"].includes(claim.status)
  );
  const claim = [...(active.length ? active : matches)].sort((a, b) => b.id - a.id)[0];
  if (!claim) throw new CliError(`Custom domain '${rawHostname}' not found`);
  return claim;
}

export async function createDomainClaim(
  siteName: string,
  hostname: string,
  cliOptions: CliOptions = {}
): Promise<DomainClaim> {
  const response = await domainRequest(
    `/sites/${encodeURIComponent(siteName)}/domains`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hostname }),
    },
    cliOptions
  );
  if (!response.ok) {
    throw new CliError(await errorMessage(response, "Could not add custom domain"));
  }
  const claim: unknown = await response.json();
  if (!isClaim(claim)) {
    throw new CliError("Server returned an invalid custom-domain response");
  }
  return claim;
}

export async function checkDomainClaim(
  siteName: string,
  claimId: number,
  cliOptions: CliOptions = {}
): Promise<DomainClaim> {
  const response = await domainRequest(
    `/sites/${encodeURIComponent(siteName)}/domains/${claimId}/check`,
    { method: "POST" },
    cliOptions
  );
  if (!response.ok) {
    const retryAfter = response.headers.get("Retry-After");
    throw new CliError(
      await errorMessage(response, "Could not check custom domain"),
      retryAfter ? `Retry in ${retryAfter} seconds` : undefined
    );
  }
  const claim: unknown = await response.json();
  if (!isClaim(claim)) {
    throw new CliError("Server returned an invalid custom-domain response");
  }
  return claim;
}

export async function cancelDomainClaim(
  siteName: string,
  claimId: number,
  cliOptions: CliOptions = {}
): Promise<202 | 204> {
  const response = await domainRequest(
    `/sites/${encodeURIComponent(siteName)}/domains/${claimId}`,
    { method: "DELETE" },
    cliOptions
  );
  if (response.status !== 202 && response.status !== 204) {
    throw new CliError(await errorMessage(response, "Could not remove custom domain"));
  }
  return response.status;
}

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
    `  Site:        ${claim.site_name ?? "Deleted site"}`,
    `  Ownership:   ${ownership(claim)}`,
    `  Routing DNS: ${routingDns(claim)}`,
    `  Router:      ${routerStatus(claim)}`,
    `  Origin TLS:  ${originTls(claim)}`,
    `  Public TLS:  ${active ? "Active" : "Not active"}`,
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

function isActive(claim: DomainClaim): boolean {
  return (
    claim.status === "verified" &&
    claim.route_status === "routed" &&
    claim.activated_at !== null
  );
}
