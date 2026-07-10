import { Command } from "commander";
import { getOptions, authHeaders, apiRequest, CliError, errorMessage } from "../lib.js";
import {
  clearCredential,
  getCredential,
  loadConfig,
  saveConfig,
  setCredential,
} from "../credentials.js";

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
    throw new CliError(await errorMessage(deviceResponse, "Failed to start login"));
  }

  const deviceData = await deviceResponse.json();

  console.log(`\nVisit: ${deviceData.verification_uri}`);
  console.log(`Enter code: ${deviceData.user_code}\n`);
  console.log("Waiting for authorization...");

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

    if (!pollResponse.ok) {
      throw new CliError(await errorMessage(pollResponse, "Login failed"));
    }

    const pollData = await pollResponse.json();

    if (pollData.status === "pending") {
      continue;
    }

    if (pollData.error) {
      throw new CliError(pollData.error);
    }

    if (pollData.status === "complete") {
      const config = loadConfig();
      setCredential(config, options.server, pollData.token);
      saveConfig(config);
      console.log(`\nLogged in as ${pollData.user.login}`);
      return;
    }
  }

  throw new CliError("Login timed out");
}

export async function logout() {
  const options = getOptions();
  const config = loadConfig();
  const token = getCredential(config, options.server);

  if (!token) {
    console.log(`Not logged in to ${options.server}`);
    if (options.token) {
      console.log(
        "Note: logout only clears credentials stored by 'buzz login'; --token and BUZZ_TOKEN are unaffected"
      );
    }
    return;
  }

  let revoked = false;
  try {
    const response = await fetch(`${options.server}/auth/logout`, {
      method: "POST",
      headers: authHeaders(token),
    });
    // 400 means the session is already invalid on the server.
    revoked = response.ok || response.status === 400;
  } catch {
    revoked = false;
  }

  clearCredential(config, options.server);
  saveConfig(config);

  if (revoked) {
    console.log("Logged out");
  } else {
    console.log(
      `Logged out locally, but the session on ${options.server} could not be revoked`
    );
  }
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
