import { Command } from "commander";
import { getOptions, loadConfig, saveConfig, authHeaders, apiRequest, CliError } from "../lib.js";

export async function login() {
  const options = getOptions();

  let deviceResponse: Response;
  try {
    deviceResponse = await fetch(`${options.server}/auth/device`, {
      method: "POST",
    });
  } catch (error) {
    throw new CliError(
      `Could not connect to server - ${error instanceof Error ? error.message : error}`
    );
  }

  if (!deviceResponse.ok) {
    const data = await deviceResponse.json();
    throw new CliError(data.error || "Failed to start login");
  }

  const deviceData = await deviceResponse.json();

  console.log(`\nVisit: ${deviceData.verification_uri}`);
  console.log(`Enter code: ${deviceData.user_code}\n`);
  console.log("Waiting for authorization...");

  // Poll for completion
  const interval = (deviceData.interval || 5) * 1000;
  const maxAttempts = Math.ceil((deviceData.expires_in || 900) / (deviceData.interval || 5));

  for (let i = 0; i < maxAttempts; i++) {
    await new Promise((resolve) => setTimeout(resolve, interval));

    let pollResponse: Response;
    try {
      pollResponse = await fetch(`${options.server}/auth/device/poll`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_code: deviceData.device_code }),
      });
    } catch (error) {
      throw new CliError(
        `Could not connect to server - ${error instanceof Error ? error.message : error}`
      );
    }

    const pollData = await pollResponse.json();

    if (pollData.status === "pending") {
      continue;
    }

    if (pollData.error) {
      throw new CliError(pollData.error);
    }

    if (pollData.status === "complete") {
      // Save token
      const config = loadConfig();
      config.token = pollData.token;
      saveConfig(config);
      console.log(`\nLogged in as ${pollData.user.login}`);
      return;
    }
  }

  throw new CliError("Login timed out");
}

export async function logout() {
  const options = getOptions();

  if (!options.token) {
    console.log("Not logged in");
    return;
  }

  try {
    await fetch(`${options.server}/auth/logout`, {
      method: "POST",
      headers: authHeaders(options.token),
    });
  } catch {
    // Ignore errors - we're logging out anyway
  }

  // Clear token from config
  const config = loadConfig();
  delete config.token;
  saveConfig(config);
  console.log("Logged out");
}

export async function whoami() {
  const response = await apiRequest("/auth/me");
  const user = await response.json();
  console.log(`Logged in as ${user.login}${user.name ? ` (${user.name})` : ""}`);
}

export function registerAuthCommands(program: Command) {
  program
    .command("login")
    .description("Login with GitHub OAuth")
    .action(login);

  program
    .command("logout")
    .description("Logout and clear session")
    .action(logout);

  program
    .command("whoami")
    .description("Show current logged-in user")
    .action(whoami);
}
