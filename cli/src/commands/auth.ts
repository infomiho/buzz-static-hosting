import { Command } from "commander";
import {
  apiFetch,
  authHeaders,
  getOptions,
  isRecord,
  requestJson,
  CliError,
  type CliOptions,
} from "../client.js";
import {
  clearCredential,
  getCredential,
  loadConfig,
  saveConfig,
  setCredential,
} from "../credentials.js";

interface DeviceStart {
  verification_uri: string;
  user_code: string;
  device_code: string;
  interval?: number;
  expires_in?: number;
}

function isDeviceStart(value: unknown): value is DeviceStart {
  return (
    isRecord(value) &&
    typeof value.verification_uri === "string" &&
    typeof value.user_code === "string" &&
    typeof value.device_code === "string" &&
    (value.interval === undefined || typeof value.interval === "number") &&
    (value.expires_in === undefined || typeof value.expires_in === "number")
  );
}

interface PollResult {
  status?: string;
  error?: string;
  token?: string;
  user?: { login: string; name?: string | null };
}

function isPollResult(value: unknown): value is PollResult {
  if (!isRecord(value)) return false;
  if (value.status === "complete") {
    return (
      typeof value.token === "string" &&
      isRecord(value.user) &&
      typeof value.user.login === "string"
    );
  }
  return true;
}

interface AuthUser {
  login: string;
  name?: string | null;
}

function isUser(value: unknown): value is AuthUser {
  return isRecord(value) && typeof value.login === "string";
}

export async function login(cliOptions: CliOptions = {}) {
  const options = getOptions(cliOptions);

  const start = await requestJson(
    "/auth/device",
    {
      guard: isDeviceStart,
      invalid: "Server returned an invalid device authorization response",
    },
    { method: "POST" },
    { auth: "none", cliOptions, errors: { fallback: "Failed to start login" } }
  );

  console.log(`\nVisit: ${start.verification_uri}`);
  console.log(`Enter code: ${start.user_code}\n`);
  console.log("Waiting for authorization...");

  const interval = (start.interval || 5) * 1000;
  const maxAttempts = Math.ceil((start.expires_in || 900) / (start.interval || 5));

  for (let i = 0; i < maxAttempts; i++) {
    await new Promise((resolve) => setTimeout(resolve, interval));

    const poll = await requestJson(
      "/auth/device/poll",
      { guard: isPollResult, invalid: "Server returned an invalid login response" },
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_code: start.device_code }),
      },
      { auth: "none", cliOptions, errors: { fallback: "Login failed" } }
    );

    if (poll.status === "pending") {
      continue;
    }

    if (poll.error) {
      throw new CliError(poll.error);
    }

    if (poll.status === "complete" && poll.token && poll.user) {
      const config = loadConfig();
      setCredential(config, options.server, poll.token);
      saveConfig(config);
      console.log(`\nLogged in as ${poll.user.login}`);
      return;
    }
  }

  throw new CliError("Login timed out");
}

export async function logout(cliOptions: CliOptions = {}) {
  const options = getOptions(cliOptions);
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
    const response = await apiFetch(
      "/auth/logout",
      { method: "POST", headers: authHeaders(token) },
      { auth: "none", cliOptions }
    );
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

export async function whoami(cliOptions: CliOptions = {}) {
  const user = await requestJson(
    "/auth/me",
    { guard: isUser, invalid: "Server returned an invalid user response" },
    {},
    { cliOptions }
  );
  console.log(`Logged in as ${user.login}${user.name ? ` (${user.name})` : ""}`);
}

export function registerAuthCommands(program: Command) {
  program
    .command("login")
    .description("Sign in with GitHub")
    .action(() => login(program.opts()));

  program
    .command("logout")
    .description("Sign out and clear the stored session")
    .action(() => logout(program.opts()));

  program
    .command("whoami")
    .description("Show the current signed-in user")
    .action(() => whoami(program.opts()));
}
