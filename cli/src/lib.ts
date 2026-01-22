import { program } from "commander";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import cliProgress from "cli-progress";

export const CONFIG_PATH = join(homedir(), ".buzz.config.json");
export const DEFAULT_SERVER = "http://localhost:8080";

export interface Config {
  server?: string;
  token?: string;
}

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

export function loadConfig(): Config {
  if (existsSync(CONFIG_PATH)) {
    try {
      return JSON.parse(readFileSync(CONFIG_PATH, "utf-8"));
    } catch {
      return {};
    }
  }
  return {};
}

export function saveConfig(config: Config): void {
  writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2) + "\n");
}

export function getOptions(): Options {
  const config = loadConfig();
  const opts = program.opts();
  return {
    server: opts.server || process.env.BUZZ_SERVER || config.server || DEFAULT_SERVER,
    token: opts.token || process.env.BUZZ_TOKEN || config.token,
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

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public code?: string
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export class CliError extends Error {
  constructor(message: string, public tip?: string) {
    super(message);
    this.name = "CliError";
  }
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
    const data = await response.json();
    throw new ApiError(data.error || "Permission denied", 403);
  }

  return response;
}

export interface ProgressCallbacks {
  onProgress?: (processed: number, total: number) => void;
}

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

    archive.directory(directory, false);
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
