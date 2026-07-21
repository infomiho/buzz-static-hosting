import { Command } from "commander";
import { requestEmpty, type CliOptions } from "../client.js";
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

  await requestEmpty(
    `/sites/${subdomain}`,
    [204],
    { method: "DELETE" },
    {
      cliOptions,
      errors: { notFound: `Site '${subdomain}' not found`, fallback: "Unknown error" },
    }
  );
  console.log(`Deleted ${subdomain}`);
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
