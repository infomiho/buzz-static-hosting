import { createRequire } from "module";
import { program } from "commander";
import { registerCommands } from "./commands/index.js";
import { CliError, handleError } from "./lib.js";

const require = createRequire(import.meta.url);
const { version } = require("../package.json");

program
  .name("buzz")
  .description("CLI for deploying static sites to Buzz hosting")
  .version(version)
  .option("-s, --server <url>", "Server URL (overrides config)")
  .option("-t, --token <token>", "Auth token (overrides config)");

registerCommands(program);

async function main() {
  await program.parseAsync();
}

main().catch(handleError);
