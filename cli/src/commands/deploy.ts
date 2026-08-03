import { Command } from "commander";
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { getOptions, CliError, type CliOptions } from "../client.js";
import { checkServerCompatibility } from "../compat.js";
import { createProgressBar, createSpinner, formatSize } from "../lib.js";
import {
  resolveSubdomain,
  packSite,
  uploadSite,
  type UploadResult,
} from "../deploy.js";

export async function deploy(
  directory: string,
  subdomain: string | undefined,
  cliOptions: CliOptions = {},
  makePrivate = false
) {
  const options = getOptions(cliOptions);

  if (!options.token) {
    throw new CliError("Not authenticated", "Run 'buzz login' first");
  }

  await checkServerCompatibility(cliOptions);

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

  let result: UploadResult;
  try {
    result = await uploadSite(
      options.server,
      options.token,
      zipBuffer,
      subdomain,
      globalThis.fetch,
      makePrivate
    );
    uploadSpinner.stop("✓ Uploaded");
  } catch (error) {
    uploadSpinner.stop("✗ Upload failed");
    throw error;
  }

  console.log(
    `Deployed to ${result.url} (${result.private ? "private" : "public"})`
  );
  writeFileSync(join(process.cwd(), "CNAME"), result.subdomain + "\n");

  if (makePrivate && !result.private) {
    throw new CliError(
      `${result.url} was published publicly, but you asked for a private site.`,
      `Run 'buzz access private --site ${result.subdomain}' to protect it, or 'buzz delete ${result.subdomain}' to remove the public copy.`
    );
  }
}

export function registerDeployCommand(program: Command) {
  program
    .command("deploy <directory>")
    .description("Deploy a directory to the server")
    .option("--site <name>", "Site name to create or replace")
    .option("--private", "Publish the site so only you can view it")
    .action(
      (directory: string, cmdOptions: { site?: string; private?: boolean }) =>
        deploy(directory, cmdOptions.site, program.opts(), cmdOptions.private)
    );
}
