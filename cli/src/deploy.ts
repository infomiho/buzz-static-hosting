import { existsSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { CliError, authHeaders, createZipBuffer, errorMessage } from "./lib.js";

interface UploadResult {
  url: string;
  subdomain: string;
}

interface DeploymentResponse {
  name: string;
  url: string;
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
  const body = new FormData();
  body.append("file", new Blob([zip], { type: "application/zip" }), "site.zip");

  const headers = authHeaders(token);
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

  if (response.ok) {
    const data = (await response.json()) as DeploymentResponse;
    if (typeof data.name !== "string" || typeof data.url !== "string") {
      throw new CliError("Server returned an invalid deployment response");
    }
    return { url: data.url, subdomain: data.name };
  }

  if (response.status === 401) {
    throw new CliError("Not authenticated", "Run 'buzz login' first");
  }

  const message = await errorMessage(response, "Unknown error");
  if (response.status === 403) {
    const tip = message.includes("owned by another user")
      ? "Choose a different subdomain with --subdomain <name>"
      : undefined;
    throw new CliError(message || "Permission denied", tip);
  }

  throw new CliError(message);
}
