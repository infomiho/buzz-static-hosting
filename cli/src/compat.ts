import { getOptions, isRecord, requestJson, type CliOptions } from "./client.js";
import { CliError } from "./errors.js";
import { cliVersion } from "./version.js";

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

function versionParts(version: string): [number, number, number] {
  const [major = 0, minor = 0, patch = 0] = version
    .split(".")
    .map((part) => Number.parseInt(part, 10) || 0);
  return [major, minor, patch];
}

function isOlderVersion(version: string, than: string): boolean {
  const left = versionParts(version);
  const right = versionParts(than);
  for (let i = 0; i < 3; i++) {
    if (left[i] !== right[i]) {
      return left[i] < right[i];
    }
  }
  return false;
}

export async function checkServerCompatibility(
  cliOptions: CliOptions = {},
  fetchFn?: typeof fetch
): Promise<void> {
  let info: ServerVersionInfo;
  try {
    info = await requestJson(
      "/version",
      { guard: isServerVersionInfo, invalid: "Invalid version response" },
      { signal: AbortSignal.timeout(5000) },
      { auth: "none", cliOptions, fetchFn }
    );
  } catch {
    // Unreachable, stalled, pre-0.3.0, or non-Buzz server: the real request reports it.
    return;
  }

  if (isOlderVersion(cliVersion, info.min_cli_version)) {
    const { server } = getOptions(cliOptions);
    throw new CliError(
      `Buzz CLI ${cliVersion} is older than the minimum version this server supports (${info.min_cli_version}). ${server} runs Buzz ${info.version}.`,
      `Install @infomiho/buzz-cli@${info.min_cli_version} or later: npm install --global @infomiho/buzz-cli. In CI, raise the pinned version.`
    );
  }
}
