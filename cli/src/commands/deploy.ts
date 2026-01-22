import { Command } from "commander";
import { existsSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import {
  getOptions,
  authHeaders,
  createZipBuffer,
  createProgressBar,
  createSpinner,
  formatSize,
  CliError,
} from "../lib.js";

export async function deploy(directory: string, subdomain: string | undefined) {
  const options = getOptions();

  if (!options.token) {
    throw new CliError("Not authenticated", "Run 'buzz login' first");
  }

  if (!existsSync(directory)) {
    throw new CliError(`'${directory}' does not exist`);
  }

  const stat = statSync(directory);
  if (!stat.isDirectory()) {
    throw new CliError(`'${directory}' is not a directory`);
  }

  // Check for CNAME file if no subdomain specified (check cwd first, then directory)
  const cwdCnamePath = join(process.cwd(), "CNAME");
  if (!subdomain && existsSync(cwdCnamePath)) {
    subdomain = readFileSync(cwdCnamePath, "utf-8").trim();
  } else if (!subdomain) {
    const dirCnamePath = join(directory, "CNAME");
    if (existsSync(dirCnamePath)) {
      subdomain = readFileSync(dirCnamePath, "utf-8").trim();
    }
  }

  const progressBar = createProgressBar("Zipping");
  let progressStarted = false;

  let zipBuffer: Buffer;
  try {
    zipBuffer = await createZipBuffer(directory, {
      onProgress: (processed, total) => {
        if (!progressStarted && total > 0) {
          progressBar.start(total, 0);
          progressStarted = true;
        }
        if (progressStarted) {
          progressBar.update(processed);
        }
      },
    });
  } finally {
    if (progressStarted) {
      progressBar.stop();
    }
  }

  console.log(`Compressed to ${formatSize(zipBuffer.length)}`);

  const uploadSpinner = createSpinner("Uploading");
  uploadSpinner.start();

  const boundary = "----BuzzFormBoundary" + Math.random().toString(36).slice(2);
  const header = `--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="site.zip"\r\nContent-Type: application/zip\r\n\r\n`;
  const footer = `\r\n--${boundary}--\r\n`;

  const body = Buffer.concat([
    Buffer.from(header),
    zipBuffer,
    Buffer.from(footer),
  ]);

  const headers: Record<string, string> = {
    "Content-Type": `multipart/form-data; boundary=${boundary}`,
    ...authHeaders(options.token),
  };
  if (subdomain) {
    headers["x-subdomain"] = subdomain;
  }

  let response: Response;
  try {
    response = await fetch(`${options.server}/deploy`, {
      method: "POST",
      headers,
      body,
    });
  } catch (error) {
    uploadSpinner.stop("✗ Upload failed");
    throw new CliError(
      `Could not connect to server - ${error instanceof Error ? error.message : error}`
    );
  }

  const data = await response.json();

  if (response.ok) {
    uploadSpinner.stop("✓ Uploaded");
    console.log(`Deployed to ${data.url}`);
    // Save subdomain to CNAME file in cwd
    const deployedSubdomain = new URL(data.url).hostname.split(".")[0];
    writeFileSync(cwdCnamePath, deployedSubdomain + "\n");
    return;
  }

  uploadSpinner.stop("✗ Upload failed");

  if (response.status === 401) {
    throw new CliError("Not authenticated", "Run 'buzz login' first");
  }

  if (response.status === 403) {
    const tip = data.error?.includes("owned by another user")
      ? "Choose a different subdomain with --subdomain <name>"
      : undefined;
    throw new CliError(data.error, tip);
  }

  throw new CliError(data.error || "Unknown error");
}

export function registerDeployCommand(program: Command) {
  program
    .command("deploy <directory>")
    .description("Deploy a directory to the server")
    .option("--subdomain <name>", "Subdomain for the site")
    .action((directory: string, cmdOptions: { subdomain?: string }) =>
      deploy(directory, cmdOptions.subdomain)
    );
}
