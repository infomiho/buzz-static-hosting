import { Command } from "commander";
import { loadConfig, saveConfig, CONFIG_PATH } from "../lib.js";

export function configCommand(key?: string, value?: string) {
  const config = loadConfig();

  if (!key) {
    // Show current config
    if (Object.keys(config).length === 0) {
      console.log("No configuration set");
      console.log(`\nConfig file: ${CONFIG_PATH}`);
      console.log("\nUsage:");
      console.log("  buzz config server <url>    Set server URL");
      return;
    }
    console.log("Current configuration:");
    if (config.server) console.log(`  server: ${config.server}`);
    if (config.token) console.log(`  token: ${config.token.slice(0, 16)}...`);
    console.log(`\nConfig file: ${CONFIG_PATH}`);
    return;
  }

  if (key === "server" && value) {
    config.server = value;
    saveConfig(config);
    console.log(`Server set to ${value}`);
  } else {
    console.error("Usage: buzz config server <url>");
    console.error("Use 'buzz login' to authenticate");
    process.exit(1);
  }
}

export function registerConfigCommand(program: Command) {
  program
    .command("config [key] [value]")
    .description("View or set configuration (server)")
    .action(configCommand);
}
