import { Command } from "commander";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { CliError, getOptions, type CliOptions } from "../client.js";

export function url(cliOptions: CliOptions = {}) {
  const cnamePath = join(process.cwd(), "CNAME");
  if (!existsSync(cnamePath)) {
    throw new CliError("No CNAME file found", "Deploy first with: buzz deploy .");
  }
  const subdomain = readFileSync(cnamePath, "utf-8").trim();
  const { server } = getOptions(cliOptions);
  try {
    const parsedServer = new URL(server);
    console.log(`${parsedServer.protocol}//${subdomain}.${parsedServer.host}`);
  } catch {
    console.log(`http://${subdomain}.localhost:8080`);
  }
}

export function registerUrlCommand(program: Command) {
  program
    .command("url")
    .description("Show the URL for the current directory")
    .action(() => url(program.opts()));
}
