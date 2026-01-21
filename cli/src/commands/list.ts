import { Command } from "commander";
import { apiRequest, formatSize, ApiError, Site } from "../lib.js";

export async function list() {
  try {
    const response = await apiRequest("/sites");
    const sites: Site[] = await response.json();

    if (sites.length === 0) {
      console.log("No sites deployed");
      return;
    }

    console.log(
      `${"NAME".padEnd(24)} ${"CREATED".padEnd(20)} ${"SIZE".padEnd(10)}`
    );
    for (const site of sites) {
      const created = site.created.slice(0, 19).replace("T", " ");
      console.log(
        `${site.name.padEnd(24)} ${created.padEnd(20)} ${formatSize(site.size_bytes).padEnd(10)}`
      );
    }
  } catch (error) {
    if (error instanceof ApiError) {
      console.error(`Error: ${error.message}`);
      process.exit(1);
    }
    throw error;
  }
}

export function registerListCommand(program: Command) {
  program
    .command("list")
    .description("List all deployed sites")
    .action(list);
}
