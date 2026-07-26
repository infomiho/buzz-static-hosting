import { Command } from "commander";
import { registerDeployCommand } from "./deploy.js";
import { registerListCommand } from "./list.js";
import { registerDeleteCommand } from "./delete.js";
import { registerConfigCommand } from "./config.js";
import { registerUrlCommand } from "./url.js";
import { registerAuthCommands } from "./auth.js";
import { registerTokensCommand } from "./tokens.js";
import { registerDomainsCommand } from "./domains.js";
import { registerAccessCommand } from "./access.js";

export function registerCommands(program: Command) {
  program.commandsGroup("Sites:");
  registerDeployCommand(program);
  registerListCommand(program);
  registerUrlCommand(program);
  registerDeleteCommand(program);

  program.commandsGroup("Site settings:");
  registerAccessCommand(program);
  registerDomainsCommand(program);

  program.commandsGroup("Automation:");
  registerTokensCommand(program);

  program.commandsGroup("Account:");
  registerAuthCommands(program);
  registerConfigCommand(program);
}
