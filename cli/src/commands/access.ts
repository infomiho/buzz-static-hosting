import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { Command } from "commander";
import {
  CliError,
  isRecord,
  requestEmpty,
  requestJson,
  type ApiErrors,
  type CliOptions,
} from "../client.js";
import { confirm } from "../prompts.js";

interface AccessState {
  private: boolean;
}

interface SiteOption {
  site?: string;
}

interface PublicOptions extends SiteOption {
  yes?: boolean;
}

interface PublicDependencies {
  confirm: (message: string) => Promise<boolean>;
}

const accessErrors: ApiErrors = {
  forbidden: (message) =>
    message === "Deploy tokens cannot perform this operation"
      ? new CliError(
          "Deployment tokens cannot manage site access",
          "Run 'buzz login' and retry with a full session"
        )
      : new CliError(message),
};

function isAccessState(value: unknown): value is AccessState {
  return isRecord(value) && typeof value.private === "boolean";
}

function accessPath(site: string): string {
  return `/sites/${encodeURIComponent(site)}/access`;
}

function resolveSite(site?: string): string {
  if (site?.trim()) return site.trim();

  const cnamePath = join(process.cwd(), "CNAME");
  if (!existsSync(cnamePath)) {
    throw new CliError(
      "No CNAME file found",
      "Deploy first with 'buzz deploy .' or pass --site <site>"
    );
  }
  const cname = readFileSync(cnamePath, "utf8").trim();
  if (!cname) throw new CliError("CNAME file is empty", "Pass --site <site>");
  return cname;
}

function printAccess(site: string, state: AccessState): void {
  console.log(`${site}: ${state.private ? "private" : "public"}`);
}

async function reportVisibility(
  site: string,
  cliOptions: CliOptions,
  init: RequestInit,
  fallback: string
): Promise<void> {
  const state = await requestJson(
    accessPath(site),
    { guard: isAccessState, invalid: "Server returned an invalid site-access response" },
    init,
    {
      cliOptions,
      errors: { ...accessErrors, notFound: `Site '${site}' not found`, fallback },
    }
  );
  printAccess(site, state);
}

export async function accessStatus(
  options: SiteOption,
  cliOptions: CliOptions = {}
): Promise<void> {
  const site = resolveSite(options.site);
  await reportVisibility(site, cliOptions, {}, "Could not get site access");
}

export async function makePrivate(
  options: SiteOption,
  cliOptions: CliOptions = {}
): Promise<void> {
  const site = resolveSite(options.site);
  await reportVisibility(
    site,
    cliOptions,
    { method: "PUT" },
    "Could not make the site private"
  );
}

export async function makePublic(
  options: PublicOptions,
  cliOptions: CliOptions = {},
  dependencies: PublicDependencies = { confirm }
): Promise<void> {
  const site = resolveSite(options.site);
  if (!options.yes && !(await dependencies.confirm(`Make '${site}' public?`))) {
    console.log("Aborted.");
    return;
  }
  await requestEmpty(accessPath(site), [204], { method: "DELETE" }, {
    cliOptions,
    errors: {
      ...accessErrors,
      notFound: `Site '${site}' not found`,
      fallback: "Could not make the site public",
    },
  });
  printAccess(site, { private: false });
}

const SITE_OPTION = "Site name (defaults to the current CNAME)";

export function registerAccessCommand(program: Command): void {
  const access = program
    .command("access")
    .description("Show or change who can view a site");
  // Status is a default subcommand rather than an action on `access` itself:
  // an option declared on the parent shadows the same option on every
  // subcommand, so `access public --site x` would lose its site.
  access
    .command("status", { isDefault: true })
    .description("Show who can view this site (default)")
    .option("--site <site>", SITE_OPTION)
    .action((options: SiteOption) => accessStatus(options, program.opts()));
  access
    .command("private")
    .description("Let only the site owner view this site")
    .option("--site <site>", SITE_OPTION)
    .action((options: SiteOption) => makePrivate(options, program.opts()));
  access
    .command("public")
    .description("Let anyone view this site")
    .option("--site <site>", SITE_OPTION)
    .option("-y, --yes", "Skip confirmation prompt")
    .action((options: PublicOptions) => makePublic(options, program.opts()));
}
