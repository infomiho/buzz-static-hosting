import { Command } from "commander";
import { registerCommands } from "./commands/index.js";
import { cliVersion } from "./version.js";

export function createProgram(): Command {
  const program = new Command();

  program
    .name("buzz")
    .description("Deploy static sites to a Buzz server")
    .version(cliVersion)
    .option("-s, --server <url>", "Buzz server URL (overrides config)")
    .option(
      "-t, --token <token>",
      "Session or deployment token (overrides config)"
    );

  registerCommands(program);
  return program;
}
