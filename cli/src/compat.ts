import { createRequire } from "node:module";
import { apiFetch, isRecord, type CliOptions } from "./client.js";
import { CliError } from "./errors.js";

const require = createRequire(import.meta.url);
const { version: cliVersion } = require("../package.json");

interface ServerVersionInfo {
  version: string;
  min_cli_version: string;
}

function isServerVersionInfo(value: unknown): value is ServerVersionInfo {
  return (
    isRecord(value) &&
    typeof value.version === "string" &&
    typeof value.min_cli_version === "string"
  );
}

function versionParts(version: string): number[] {
  return version.split(".").map((part) => Number.parseInt(part, 10) || 0);
}

function isOlderVersion(version: string, than: string): boolean {
  const left = versionParts(version);
  const right = versionParts(than);
  for (let i = 0; i < 3; i++) {
    if ((left[i] ?? 0) !== (right[i] ?? 0)) {
      return (left[i] ?? 0) < (right[i] ?? 0);
    }
  }
  return false;
}

export async function checkServerCompatibility(
  cliOptions: CliOptions = {},
  fetchFn?: typeof fetch
): Promise<void> {
  let response: Response;
  try {
    response = await apiFetch("/version", {}, { auth: "none", cliOptions, fetchFn });
  } catch {
    return; // An unreachable server fails the real request with a better error.
  }
  if (!response.ok) {
    return; // Servers older than 0.3.0 have no /version endpoint.
  }

  const info: unknown = await response.json().catch(() => undefined);
  if (!isServerVersionInfo(info)) {
    return;
  }

  if (isOlderVersion(cliVersion, info.min_cli_version)) {
    throw new CliError(
      `Buzz CLI ${cliVersion} is older than the minimum version this server supports (${info.min_cli_version})`,
      "Run 'npm install -g @infomiho/buzz-cli' to update"
    );
  }
}
