import { program } from "commander";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

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

export async function apiRequest(
  path: string,
  options: RequestInit = {},
  { requireAuth = true }: { requireAuth?: boolean } = {}
): Promise<Response> {
  const opts = getOptions();

  if (requireAuth && !opts.token) {
    console.error("Error: Not authenticated. Run 'buzz login' first");
    process.exit(1);
  }

  try {
    const response = await fetch(`${opts.server}${path}`, {
      ...options,
      headers: {
        ...authHeaders(opts.token),
        ...options.headers,
      },
    });

    if (response.status === 401) {
      console.error("Error: Session expired. Run 'buzz login' to re-authenticate");
      process.exit(1);
    }

    if (response.status === 403) {
      const data = await response.json();
      throw new ApiError(data.error || "Permission denied", 403);
    }

    return response;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    console.error(
      `Error: Could not connect to server - ${error instanceof Error ? error.message : error}`
    );
    process.exit(1);
  }
}

export async function createZipBuffer(directory: string): Promise<Buffer> {
  const archiver = await import("archiver");
  return new Promise((resolve, reject) => {
    const archive = archiver.default("zip", { zlib: { level: 9 } });
    const chunks: Buffer[] = [];
    archive.on("data", (chunk: Buffer) => chunks.push(chunk));
    archive.on("end", () => resolve(Buffer.concat(chunks)));
    archive.on("error", reject);
    archive.directory(directory, false);
    archive.finalize();
  });
}
