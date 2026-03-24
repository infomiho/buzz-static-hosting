import { Command } from "commander";
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import {
  getOptions,
  createProgressBar,
  createSpinner,
  formatSize,
  CliError,
} from "../lib.js";
import { resolveSubdomain, packSite, uploadSite } from "../deploy.js";

export async function deploy(directory: string, subdomain: string | undefined) {
  const options = getOptions();

  if (!options.token) {
    throw new CliError("Not authenticated", "Run 'buzz login' first");
  }

  subdomain = resolveSubdomain(process.cwd(), directory, subdomain);

  const progressBar = createProgressBar("Zipping");
  let progressStarted = false;

  let zipBuffer: Buffer;
  try {
    zipBuffer = await packSite(directory, (processed, total) => {
      if (!progressStarted && total > 0) {
        progressBar.start(total, 0);
        progressStarted = true;
      }
      if (progressStarted) {
        progressBar.update(processed);
      }
    });
  } finally {
    if (progressStarted) {
      progressBar.stop();
    }
  }

  console.log(`Compressed to ${formatSize(zipBuffer.length)}`);

  const uploadSpinner = createSpinner("Uploading");
  uploadSpinner.start();

  try {
    const result = await uploadSite(options.server, options.token, zipBuffer, subdomain);
    uploadSpinner.stop("✓ Uploaded");
    console.log(`Deployed to ${result.url}`);
    writeFileSync(join(process.cwd(), "CNAME"), result.subdomain + "\n");
  } catch (error) {
    uploadSpinner.stop("✗ Upload failed");
    throw error;
  }
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
