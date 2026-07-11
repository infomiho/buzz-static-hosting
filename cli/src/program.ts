import { createRequire } from "node:module";
import { Command } from "commander";
import { registerCommands } from "./commands/index.js";

const require = createRequire(import.meta.url);
const { version } = require("../package.json");

export function createProgram(): Command {
  const program = new Command();

  program
    .name("buzz")
    .description("Deploy static sites to a Buzz server")
    .version(version)
    .option("-s, --server <url>", "Buzz server URL (overrides config)")
    .option(
      "-t, --token <token>",
      "Session or deployment token (overrides config)"
    );

  registerCommands(program);
  return program;
}
