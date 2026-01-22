import { createInterface } from "readline";
import { Command } from "commander";
import { apiRequest, CliError } from "../lib.js";

function confirm(message: string): Promise<boolean> {
  const rl = createInterface({
    input: process.stdin,
    output: process.stdout,
  });

  return new Promise((resolve) => {
    rl.question(`${message} [y/N] `, (answer) => {
      rl.close();
      resolve(answer.toLowerCase() === "y");
    });
  });
}

export async function deleteSite(
  subdomain: string,
  options: { yes?: boolean }
) {
  if (!options.yes) {
    const confirmed = await confirm(`Delete site '${subdomain}'?`);
    if (!confirmed) {
      console.log("Aborted.");
      return;
    }
  }

  const response = await apiRequest(`/sites/${subdomain}`, { method: "DELETE" });

  if (response.status === 204) {
    console.log(`Deleted ${subdomain}`);
    return;
  }

  if (response.status === 404) {
    throw new CliError(`Site '${subdomain}' not found`);
  }

  const data = await response.json();
  throw new CliError(data.error || "Unknown error");
}

export function registerDeleteCommand(program: Command) {
  program
    .command("delete <subdomain>")
    .description("Delete a deployed site")
    .option("-y, --yes", "Skip confirmation prompt")
    .action(deleteSite);
}
