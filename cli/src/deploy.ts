import { existsSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { CliError, isRecord, requestJson } from "./client.js";
import { createZipBuffer } from "./lib.js";

interface UploadResult {
  url: string;
  subdomain: string;
}

interface DeploymentResponse {
  name: string;
  url: string;
}

function isDeploymentResponse(value: unknown): value is DeploymentResponse {
  return (
    isRecord(value) &&
    typeof value.name === "string" &&
    typeof value.url === "string"
  );
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

  const headers: Record<string, string> = {};
  if (subdomain) {
    headers["x-subdomain"] = subdomain;
  }

  const data = await requestJson(
    "/deploy",
    {
      guard: isDeploymentResponse,
      invalid: "Server returned an invalid deployment response",
    },
    { method: "POST", headers, body },
    {
      fetchFn,
      cliOptions: { server, token },
      errors: {
        unauthorized: new CliError("Not authenticated", "Run 'buzz login' first"),
        forbidden: (message) =>
          new CliError(
            message,
            message.includes("owned by another user")
              ? "Choose a different subdomain with --subdomain <name>"
              : undefined
          ),
        fallback: "Unknown error",
      },
    }
  );

  return { url: data.url, subdomain: data.name };
}
