import { Command } from "commander";
import { apiRequest, CliError } from "../lib.js";

export async function deleteSite(subdomain: string) {
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
    .action(deleteSite);
}
