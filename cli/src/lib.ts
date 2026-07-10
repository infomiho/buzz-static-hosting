import { program } from "commander";
import cliProgress from "cli-progress";
import { CliError } from "./errors.js";
import {
  DEFAULT_SERVER,
  getCredential,
  loadConfig,
  normalizeServerUrl,
} from "./credentials.js";

export { CliError } from "./errors.js";

export interface Site {
  name: string;
  created: string;
  size_bytes: number;
}

export interface Options {
  server: string;
  token?: string;
}

export interface DeploymentToken {
  id: string;
  name: string;
  site_name: string;
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
}

export function getOptions(): Options {
  const config = loadConfig();
  const opts = program.opts();
  const server = normalizeServerUrl(
    opts.server || process.env.BUZZ_SERVER || config.server || DEFAULT_SERVER
  );
  return {
    server,
    token: opts.token || process.env.BUZZ_TOKEN || getCredential(config, server),
  };
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

export function authHeaders(token?: string): Record<string, string> {
  if (token) {
    return { Authorization: `Bearer ${token}` };
  }
  return {};
}

export function handleError(error: unknown): void {
  if (error instanceof CliError) {
    console.error(`Error: ${error.message}`);
    if (error.tip) {
      console.error(`Tip: ${error.tip}`);
    }
  } else if (error instanceof Error) {
    console.error(`Error: ${error.message}`);
  } else {
    console.error(`Error: ${String(error ?? "Unknown error")}`);
  }
  process.exitCode = 1;
}

export async function errorMessage(response: Response, fallback: string): Promise<string> {
  const text = await response.text();
  if (!text) return fallback;

  try {
    const data: unknown = JSON.parse(text);
    if (data && typeof data === "object") {
      const { detail, error } = data as { detail?: unknown; error?: unknown };
      if (typeof detail === "string") return detail;
      if (typeof error === "string") return error;
    }
  } catch {
    return text;
  }

  return fallback;
}

export async function apiRequest(
  path: string,
  options: RequestInit = {},
  { requireAuth = true }: { requireAuth?: boolean } = {}
): Promise<Response> {
  const opts = getOptions();

  if (requireAuth && !opts.token) {
    throw new CliError("Not authenticated", "Run 'buzz login' first");
  }

  let response: Response;
  try {
    response = await fetch(`${opts.server}${path}`, {
      ...options,
      headers: {
        ...authHeaders(opts.token),
        ...options.headers,
      },
    });
  } catch (error) {
    throw new CliError(
      `Could not connect to server - ${error instanceof Error ? error.message : error}`
    );
  }

  if (response.status === 401) {
    throw new CliError("Session expired", "Run 'buzz login' to re-authenticate");
  }

  if (response.status === 403) {
    throw new CliError(await errorMessage(response, "Permission denied"));
  }

  return response;
}

export interface ProgressCallbacks {
  onProgress?: (processed: number, total: number) => void;
}

const IGNORED_DIRS = [".git", "node_modules", ".vscode", ".idea"];
const IGNORED_FILES = ["**/.DS_Store", "**/.env", "**/.env.*"];

export async function createZipBuffer(
  directory: string,
  callbacks?: ProgressCallbacks
): Promise<Buffer> {
  const archiver = await import("archiver");
  return new Promise((resolve, reject) => {
    const archive = archiver.default("zip", { zlib: { level: 9 } });
    const chunks: Buffer[] = [];

    archive.on("data", (chunk: Buffer) => chunks.push(chunk));
    archive.on("end", () => resolve(Buffer.concat(chunks)));
    archive.on("error", reject);
    archive.on("progress", (progress) => {
      callbacks?.onProgress?.(progress.entries.processed, progress.entries.total);
    });

    archive.glob("**/*", {
      cwd: directory,
      dot: true,
      skip: IGNORED_DIRS,
      ignore: IGNORED_FILES,
    });
    archive.finalize();
  });
}

export function isCI(): boolean {
  return !process.stdout.isTTY || !!process.env.CI;
}

export function createProgressBar(task: string): cliProgress.SingleBar {
  const ci = isCI();
  return new cliProgress.SingleBar({
    format: `${task} [{bar}] {percentage}% | {value}/{total} files`,
    barCompleteChar: "█",
    barIncompleteChar: "░",
    barsize: 20,
    hideCursor: true,
    clearOnComplete: false,
    noTTYOutput: ci,
    notTTYSchedule: ci ? 2000 : 100,
  });
}

export function createSpinner(message: string): { start: () => void; stop: (finalMessage: string) => void } {
  const ci = isCI();
  const frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];
  let frameIndex = 0;
  let interval: NodeJS.Timeout | null = null;

  return {
    start: () => {
      if (ci) {
        console.log(`${message}...`);
        return;
      }
      process.stdout.write(`${frames[0]} ${message}`);
      interval = setInterval(() => {
        frameIndex = (frameIndex + 1) % frames.length;
        process.stdout.write(`\r${frames[frameIndex]} ${message}`);
      }, 80);
    },
    stop: (finalMessage: string) => {
      if (interval) {
        clearInterval(interval);
        process.stdout.write(`\r\x1b[K${finalMessage}\n`);
      } else if (ci) {
        console.log(finalMessage);
      }
    },
  };
}
