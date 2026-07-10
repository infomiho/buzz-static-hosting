import { Command } from "commander";
import { CliError } from "../lib.js";
import { CONFIG_PATH, loadConfig, normalizeServerUrl, saveConfig } from "../credentials.js";

export function configCommand(key?: string, value?: string) {
  const config = loadConfig();

  if (!key) {
    // Show current config
    const credentials = config.credentials ?? {};
    const servers = Object.keys(credentials);
    if (!config.server && servers.length === 0) {
      console.log("No configuration set");
      console.log(`\nConfig file: ${CONFIG_PATH}`);
      console.log("\nUsage:");
      console.log("  buzz config server <url>    Set server URL");
      return;
    }
    console.log("Current configuration:");
    if (config.server) {
      let server = config.server;
      try {
        server = normalizeServerUrl(server);
      } catch {
        // show the raw value so the user can spot and fix it
      }
      console.log(`  server: ${server}`);
    }
    for (const server of servers) {
      console.log(`  token (${server}): ${credentials[server].slice(0, 16)}...`);
    }
    console.log(`\nConfig file: ${CONFIG_PATH}`);
    return;
  }

  if (key === "server" && value) {
    const server = normalizeServerUrl(value);
    config.server = server;
    saveConfig(config);
    console.log(`Server set to ${server}`);
  } else {
    throw new CliError(
      "Invalid config command",
      "Usage: buzz config server <url>"
    );
  }
}

export function registerConfigCommand(program: Command) {
  program
    .command("config [key] [value]")
    .description("View or set configuration (server)")
    .action(configCommand);
}
