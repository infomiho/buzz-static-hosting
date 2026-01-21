import { Command } from "commander";
import { apiRequest, ApiError } from "../lib.js";

export async function deleteSite(subdomain: string) {
  try {
    const response = await apiRequest(`/sites/${subdomain}`, { method: "DELETE" });

    if (response.status === 204) {
      console.log(`Deleted ${subdomain}`);
    } else if (response.status === 404) {
      console.error(`Error: Site '${subdomain}' not found`);
      process.exit(1);
    } else {
      const data = await response.json();
      console.error(`Error: ${data.error || "Unknown error"}`);
      process.exit(1);
    }
  } catch (error) {
    if (error instanceof ApiError) {
      console.error(`Error: ${error.message}`);
      process.exit(1);
    }
    throw error;
  }
}

export function registerDeleteCommand(program: Command) {
  program
    .command("delete <subdomain>")
    .description("Delete a deployed site")
    .action(deleteSite);
}
