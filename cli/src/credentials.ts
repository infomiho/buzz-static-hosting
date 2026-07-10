import { chmodSync, existsSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { CliError } from "./errors.js";

export const CONFIG_PATH = join(homedir(), ".buzz.config.json");
export const DEFAULT_SERVER = "http://localhost:8080";

export interface Config {
  server?: string;
  credentials?: Record<string, string>;
}

export function normalizeServerUrl(raw: string): string {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new CliError(
      `Invalid server URL '${raw}'`,
      "Use a full URL like https://buzz.example.com"
    );
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new CliError(`Server URL '${raw}' must use http or https`);
  }
  const path = url.pathname.replace(/\/+$/, "");
  return `${url.protocol}//${url.host}${path}`;
}

export function getCredential(config: Config, server: string): string | undefined {
  return config.credentials?.[normalizeServerUrl(server)];
}

export function setCredential(config: Config, server: string, token: string): void {
  config.credentials = { ...config.credentials, [normalizeServerUrl(server)]: token };
}

export function clearCredential(config: Config, server: string): void {
  if (!config.credentials) return;
  delete config.credentials[normalizeServerUrl(server)];
  if (Object.keys(config.credentials).length === 0) {
    delete config.credentials;
  }
}

export function loadConfig(path: string = CONFIG_PATH): Config {
  if (!existsSync(path)) return {};

  let parsed: unknown;
  try {
    parsed = JSON.parse(readFileSync(path, "utf-8"));
  } catch {
    console.error(`Warning: ignoring unreadable config file at ${path}`);
    return {};
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    console.error(`Warning: ignoring unreadable config file at ${path}`);
    return {};
  }

  const raw = parsed as Record<string, unknown>;
  const config: Config = {};
  if (typeof raw.server === "string") {
    config.server = raw.server;
  }
  if (typeof raw.credentials === "object" && raw.credentials !== null) {
    for (const [server, token] of Object.entries(raw.credentials)) {
      if (typeof token !== "string") continue;
      try {
        setCredential(config, server, token);
      } catch {
        continue;
      }
    }
  }
  // Configs written before credentials were scoped hold a single token
  // that belonged to the server the user was targeting at the time.
  if (typeof raw.token === "string") {
    try {
      const server = normalizeServerUrl(
        config.server || process.env.BUZZ_SERVER || DEFAULT_SERVER
      );
      if (getCredential(config, server) === undefined) {
        setCredential(config, server, raw.token);
      }
    } catch {
      // The legacy token has no valid server to belong to; drop it.
    }
  }
  return config;
}

export function saveConfig(config: Config, path: string = CONFIG_PATH): void {
  const tmpPath = `${path}.${process.pid}.tmp`;
  writeFileSync(tmpPath, JSON.stringify(config, null, 2) + "\n", { mode: 0o600 });
  chmodSync(tmpPath, 0o600);
  renameSync(tmpPath, path);
}
