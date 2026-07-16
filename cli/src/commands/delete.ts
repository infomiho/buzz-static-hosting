import { Command } from "commander";
import { apiRequest, CliError, errorMessage, type CliOptions } from "../lib.js";
import { confirm } from "../prompts.js";

export async function deleteSite(
  subdomain: string,
  options: { yes?: boolean },
  cliOptions: CliOptions = {}
) {
  if (!options.yes) {
    const confirmed = await confirm(`Delete site '${subdomain}'?`);
    if (!confirmed) {
      console.log("Aborted.");
      return;
    }
  }

  const response = await apiRequest(
    `/sites/${subdomain}`,
    { method: "DELETE" },
    { cliOptions }
  );

  if (response.status === 204) {
    console.log(`Deleted ${subdomain}`);
    return;
  }

  if (response.status === 404) {
    throw new CliError(`Site '${subdomain}' not found`);
  }

  throw new CliError(await errorMessage(response, "Unknown error"));
}

export function registerDeleteCommand(program: Command) {
  program
    .command("delete <subdomain>")
    .description("Delete a deployed site")
    .option("-y, --yes", "Skip confirmation prompt")
    .action((subdomain: string, options: { yes?: boolean }) =>
      deleteSite(subdomain, options, program.opts())
    );
}
