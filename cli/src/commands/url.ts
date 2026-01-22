import { Command } from "commander";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { loadConfig, DEFAULT_SERVER, CliError } from "../lib.js";

export function url() {
  const cnamePath = join(process.cwd(), "CNAME");
  if (!existsSync(cnamePath)) {
    throw new CliError("No CNAME file found", "Deploy first with: buzz deploy .");
  }
  const subdomain = readFileSync(cnamePath, "utf-8").trim();
  const config = loadConfig();
  const server = config.server || DEFAULT_SERVER;
  try {
    const host = new URL(server).hostname;
    console.log(`https://${subdomain}.${host}`);
  } catch {
    console.log(`http://${subdomain}.localhost:8080`);
  }
}

export function registerUrlCommand(program: Command) {
  program
    .command("url")
    .description("Show the URL for the current directory")
    .action(url);
}
