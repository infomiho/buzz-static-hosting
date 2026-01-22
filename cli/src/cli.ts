import { program } from "commander";
import { registerCommands } from "./commands/index.js";
import { CliError, handleError } from "./lib.js";

program
  .name("buzz")
  .description("CLI for deploying static sites to Buzz hosting")
  .version("1.0.0")
  .option("-s, --server <url>", "Server URL (overrides config)")
  .option("-t, --token <token>", "Auth token (overrides config)");

registerCommands(program);

async function main() {
  await program.parseAsync();
}

main().catch(handleError);
