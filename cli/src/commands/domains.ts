import { Command } from "commander";
import { CliError, type CliOptions } from "../lib.js";
import { confirm } from "../prompts.js";
import {
  cancelDomainTransition,
  cancelDomainClaim,
  checkDomainClaim,
  createDomainClaim,
  formatDomainClaim,
  getAllDomainClaims,
  getDomainCapability,
  getDomainClaims,
  resolveDomainClaim,
  retryDomainTransition,
  type DomainCapability,
} from "../domains.js";

function requireReady(capability: DomainCapability): void {
  if (capability.status !== "ready") {
    throw new CliError(capability.detail ?? "Custom domains are not ready on this server");
  }
}

function requireControlReady(capability: DomainCapability): void {
  if (!capability.enabled || !capability.control_ready) {
    throw new CliError(capability.detail ?? "Custom domains are not ready on this server");
  }
}

function printCapability(capability: DomainCapability): void {
  if (capability.status === "disabled") {
    console.log("Custom domains are disabled on this Buzz server.");
  } else if (capability.status === "unready") {
    console.log("Custom domains are temporarily unavailable.");
    if (capability.detail) console.log(capability.detail);
  }
}

export async function listDomains(
  siteName?: string,
  cliOptions: CliOptions = {}
): Promise<void> {
  const capability = await getDomainCapability(cliOptions);
  printCapability(capability);
  const claims = siteName
    ? await getDomainClaims(siteName, cliOptions)
    : await getAllDomainClaims(cliOptions);
  if (!claims.length) {
    console.log(siteName ? `No custom domains for site '${siteName}'` : "No custom domains");
    return;
  }
  console.log(claims.map(formatDomainClaim).join("\n\n"));
}

export async function addDomain(
  siteName: string,
  hostname: string,
  cliOptions: CliOptions = {}
): Promise<void> {
  const capability = await getDomainCapability(cliOptions);
  if (capability.automatic?.ready !== true) {
    throw new CliError(
      capability.automatic?.detail ??
        capability.detail ??
        "Custom domains are not ready on this server"
    );
  }
  const claim = await createDomainClaim(siteName, hostname, cliOptions);
  console.log(`Added custom domain ${claim.hostname} to site '${siteName}'.\n`);
  console.log("Prove ownership by adding this DNS record:");
  console.log(`  Type:  ${claim.verification.type}`);
  console.log(`  Name:  ${claim.verification.name}`);
  console.log(`  Value: ${claim.verification.value}\n`);
  console.log(
    "Point this hostname to Buzz using direct DNS or supported Cloudflare proxying:"
  );
  for (const target of capability.routing_targets) {
    console.log(`  ${target.type.padEnd(5)} ${claim.hostname} -> ${target.value}`);
  }
  console.log(
    "Use DNS-only records for a direct connection. If you use Cloudflare, keep proxying enabled and use Full (strict)."
  );
  console.log("\nBuzz does not change your DNS records.");
  console.log(`After the records propagate, run:\n  buzz domains check ${siteName} ${claim.hostname}`);
}

async function updateTransition(
  action: "retry" | "cancel",
  siteName: string,
  hostname: string,
  cliOptions: CliOptions
): Promise<void> {
  const capability = await getDomainCapability(cliOptions);
  requireControlReady(capability);
  const current = resolveDomainClaim(await getDomainClaims(siteName, cliOptions), hostname);
  const claim = await (action === "retry" ? retryDomainTransition : cancelDomainTransition)(
    siteName,
    current.id,
    cliOptions
  );
  console.log(formatDomainClaim(claim));
}

export const retryTransition = (
  siteName: string,
  hostname: string,
  cliOptions: CliOptions = {}
) => updateTransition("retry", siteName, hostname, cliOptions);

export const cancelTransition = (
  siteName: string,
  hostname: string,
  cliOptions: CliOptions = {}
) => updateTransition("cancel", siteName, hostname, cliOptions);

export async function checkDomain(
  siteName: string,
  hostname: string,
  cliOptions: CliOptions = {}
): Promise<void> {
  const capability = await getDomainCapability(cliOptions);
  requireControlReady(capability);
  const current = resolveDomainClaim(await getDomainClaims(siteName, cliOptions), hostname);
  const claim =
    current.status === "pending"
      ? await checkDomainClaim(siteName, current.id, cliOptions)
      : current;
  console.log(formatDomainClaim(claim));
}

interface RemoveDependencies {
  confirm: (message: string) => Promise<boolean>;
}

export async function removeDomain(
  siteName: string,
  hostname: string,
  options: { yes?: boolean },
  cliOptions: CliOptions = {},
  dependencies: RemoveDependencies = { confirm }
): Promise<void> {
  const capability = await getDomainCapability(cliOptions);
  if (capability.status === "disabled") requireReady(capability);
  const claim = resolveDomainClaim(await getDomainClaims(siteName, cliOptions), hostname);
  if (["expired", "cancelled"].includes(claim.status) || claim.route_status === "removed") {
    console.log(`Custom domain ${claim.hostname} is already inactive.`);
    return;
  }
  if (claim.route_status === "removing" && claim.removal_requested_at) {
    console.log(`Removal is already in progress for ${claim.hostname}.`);
    return;
  }
  if (
    !options.yes &&
    !(await dependencies.confirm(
      `Remove custom domain '${claim.hostname}' from site '${siteName}'?\n` +
        "Buzz will stop tracking ownership and routing, but will not change DNS records."
    ))
  ) {
    console.log("Aborted.");
    return;
  }
  const status = await cancelDomainClaim(siteName, claim.id, cliOptions);
  if (status === 202) {
    console.log(`Removal requested for ${claim.hostname}.`);
    console.log("The domain remains reserved until its router withdrawal is confirmed.");
  } else {
    console.log(`Removed custom domain ${claim.hostname} from site '${siteName}'.`);
    console.log("Buzz did not change its DNS records.");
  }
}

export function registerDomainsCommand(program: Command): void {
  const domains = program.command("domains").description("Manage custom domains");
  domains
    .command("list [site]")
    .description("List custom domains and lifecycle status")
    .action((site?: string) => listDomains(site, program.opts()));
  domains
    .command("add <site> <domain>")
    .description("Attach a custom domain to a site")
    .action((site: string, domain: string) =>
      addDomain(site, domain, program.opts())
    );
  domains
    .command("check <site> <domain>")
    .description("Check custom-domain ownership and activation")
    .action((site: string, domain: string) => checkDomain(site, domain, program.opts()));
  domains
    .command("retry <site> <domain>")
    .description("Retry a failed connection transition")
    .action((site: string, domain: string) =>
      retryTransition(site, domain, program.opts())
    );
  domains
    .command("cancel-transition <site> <domain>")
    .description("Cancel an active connection transition")
    .action((site: string, domain: string) =>
      cancelTransition(site, domain, program.opts())
    );
  domains
    .command("remove <site> <domain>")
    .description("Remove a custom domain without changing DNS records")
    .option("-y, --yes", "Skip confirmation prompt")
    .action((site: string, domain: string, options: { yes?: boolean }) =>
      removeDomain(site, domain, options, program.opts())
    );
}
