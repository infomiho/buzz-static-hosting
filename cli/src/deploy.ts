import { existsSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { CliError, authHeaders, createZipBuffer } from "./lib.js";

export interface UploadResult {
  url: string;
  subdomain: string;
}

export function resolveSubdomain(
  cwd: string,
  directory: string,
  explicit?: string
): string | undefined {
  if (explicit) return explicit;

  const cwdCname = join(cwd, "CNAME");
  if (existsSync(cwdCname)) {
    return readFileSync(cwdCname, "utf-8").trim();
  }

  const dirCname = join(directory, "CNAME");
  if (existsSync(dirCname)) {
    return readFileSync(dirCname, "utf-8").trim();
  }

  return undefined;
}

export async function packSite(
  directory: string,
  onProgress?: (processed: number, total: number) => void
): Promise<Buffer> {
  if (!existsSync(directory)) {
    throw new CliError(`'${directory}' does not exist`);
  }

  const stat = statSync(directory);
  if (!stat.isDirectory()) {
    throw new CliError(`'${directory}' is not a directory`);
  }

  return createZipBuffer(directory, { onProgress });
}

export async function uploadSite(
  server: string,
  token: string,
  zip: Buffer,
  subdomain?: string,
  fetchFn: typeof fetch = globalThis.fetch
): Promise<UploadResult> {
  const boundary =
    "----BuzzFormBoundary" + Math.random().toString(36).slice(2);
  const header = `--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="site.zip"\r\nContent-Type: application/zip\r\n\r\n`;
  const footer = `\r\n--${boundary}--\r\n`;

  const body = Buffer.concat([
    Buffer.from(header),
    zip,
    Buffer.from(footer),
  ]);

  const headers: Record<string, string> = {
    "Content-Type": `multipart/form-data; boundary=${boundary}`,
    ...authHeaders(token),
  };
  if (subdomain) {
    headers["x-subdomain"] = subdomain;
  }

  let response: Response;
  try {
    response = await fetchFn(`${server}/deploy`, {
      method: "POST",
      headers,
      body,
    });
  } catch (error) {
    throw new CliError(
      `Could not connect to server - ${error instanceof Error ? error.message : error}`
    );
  }

  const data = await response.json();

  if (response.ok) {
    const deployedSubdomain = new URL(data.url).hostname.split(".")[0];
    return { url: data.url, subdomain: deployedSubdomain };
  }

  if (response.status === 401) {
    throw new CliError("Not authenticated", "Run 'buzz login' first");
  }

  if (response.status === 403) {
    const tip = data.detail?.includes("owned by another user")
      ? "Choose a different subdomain with --subdomain <name>"
      : undefined;
    throw new CliError(data.detail || "Permission denied", tip);
  }

  throw new CliError(data.detail || "Unknown error");
}
