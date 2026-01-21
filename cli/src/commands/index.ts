import { Command } from "commander";
import { registerDeployCommand } from "./deploy.js";
import { registerListCommand } from "./list.js";
import { registerDeleteCommand } from "./delete.js";
import { registerConfigCommand } from "./config.js";
import { registerUrlCommand } from "./url.js";
import { registerAuthCommands } from "./auth.js";
import { registerTokensCommand } from "./tokens.js";

export function registerCommands(program: Command) {
  registerDeployCommand(program);
  registerListCommand(program);
  registerDeleteCommand(program);
  registerConfigCommand(program);
  registerUrlCommand(program);
  registerAuthCommands(program);
  registerTokensCommand(program);
}
