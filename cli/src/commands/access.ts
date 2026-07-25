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
  enabled: boolean;
  patterns: string[];
}

interface SiteOption {
  site?: string;
}

interface EnableOptions extends SiteOption {
  include: string[];
}

interface DisableOptions extends SiteOption {
  yes?: boolean;
}

interface DisableDependencies {
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
  return (
    isRecord(value) &&
    typeof value.enabled === "boolean" &&
    Array.isArray(value.patterns) &&
    value.patterns.every((pattern) => typeof pattern === "string")
  );
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

async function getAccess(site: string, cliOptions: CliOptions): Promise<AccessState> {
  return requestJson(
    accessPath(site),
    { guard: isAccessState, invalid: "Server returned an invalid site-access response" },
    {},
    {
      cliOptions,
      errors: {
        ...accessErrors,
        notFound: `Site '${site}' not found`,
        fallback: "Could not get site access",
      },
    }
  );
}

function printAccess(site: string, state: AccessState): void {
  if (!state.enabled) {
    console.log(`${site}: off`);
  } else if (state.patterns.includes("/")) {
    console.log(`${site}: entire site`);
  } else {
    console.log(`${site}:`);
    for (const pattern of state.patterns) console.log(`  ${pattern}`);
  }
}

export async function enableAccess(
  options: EnableOptions,
  cliOptions: CliOptions = {}
): Promise<void> {
  const site = resolveSite(options.site);
  const patterns = options.include.length ? options.include : ["/"];
  const state = await requestJson(
    accessPath(site),
    { guard: isAccessState, invalid: "Server returned an invalid site-access response" },
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ patterns }),
    },
    {
      cliOptions,
      errors: {
        ...accessErrors,
        notFound: `Site '${site}' not found`,
        fallback: "Could not enable site access",
      },
    }
  );
  printAccess(site, state);
}

export async function accessStatus(
  options: SiteOption,
  cliOptions: CliOptions = {}
): Promise<void> {
  const site = resolveSite(options.site);
  printAccess(site, await getAccess(site, cliOptions));
}

export async function disableAccess(
  options: DisableOptions,
  cliOptions: CliOptions = {},
  dependencies: DisableDependencies = { confirm }
): Promise<void> {
  const site = resolveSite(options.site);
  if (
    !options.yes &&
    !(await dependencies.confirm(`Disable Buzz Access for '${site}'? The site will be public.`))
  ) {
    console.log("Aborted.");
    return;
  }
  await requestEmpty(accessPath(site), [204], { method: "DELETE" }, {
    cliOptions,
    errors: {
      ...accessErrors,
      notFound: `Site '${site}' not found`,
      fallback: "Could not disable site access",
    },
  });
  console.log(`${site}: off`);
}

function collect(value: string, previous: string[]): string[] {
  return [...previous, value];
}

export function registerAccessCommand(program: Command): void {
  const access = program.command("access").description("Manage Buzz Access");
  access
    .command("enable")
    .description("Protect a site")
    .option("--site <site>", "Site name (defaults to the current CNAME)")
    .option("--include <pattern>", "Path pattern to protect (repeatable)", collect, [])
    .action((options: EnableOptions) => enableAccess(options, program.opts()));
  access
    .command("status")
    .description("Show Buzz Access")
    .option("--site <site>", "Site name (defaults to the current CNAME)")
    .action((options: SiteOption) => accessStatus(options, program.opts()));
  access
    .command("disable")
    .description("Disable Buzz Access")
    .option("--site <site>", "Site name (defaults to the current CNAME)")
    .option("-y, --yes", "Skip confirmation prompt")
    .action((options: DisableOptions) => disableAccess(options, program.opts()));
}
