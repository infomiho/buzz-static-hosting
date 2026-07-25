import { Command } from "commander";
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { getOptions, CliError, type CliOptions } from "../client.js";
import { createProgressBar, createSpinner, formatSize } from "../lib.js";
import { resolveSubdomain, packSite, uploadSite } from "../deploy.js";

export async function deploy(
  directory: string,
  subdomain: string | undefined,
  cliOptions: CliOptions = {},
  accessPatterns?: string[]
) {
  const options = getOptions(cliOptions);

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
    const result = await uploadSite(
      options.server,
      options.token,
      zipBuffer,
      subdomain,
      globalThis.fetch,
      accessPatterns
    );
    uploadSpinner.stop("✓ Uploaded");
    console.log(`Deployed to ${result.url}`);
    writeFileSync(join(process.cwd(), "CNAME"), result.subdomain + "\n");
  } catch (error) {
    uploadSpinner.stop("✗ Upload failed");
    throw error;
  }
}

function deploymentAccessPatterns(options: {
  access?: boolean;
  include: string[];
}): string[] | undefined {
  if (options.include.length && !options.access) {
    throw new CliError("--include requires --access");
  }
  if (!options.access) return undefined;
  return options.include.length ? options.include : ["/"];
}

export function registerDeployCommand(program: Command) {
  program
    .command("deploy <directory>")
    .description("Deploy a directory to the server")
    .option("--subdomain <name>", "Site name to use as the subdomain")
    .option("--access", "Enable Buzz Access as part of the deployment")
    .option(
      "--include <pattern>",
      "Buzz Access path pattern to protect (repeatable)",
      (value: string, previous: string[]) => [...previous, value],
      []
    )
    .action(
      (
        directory: string,
        cmdOptions: { subdomain?: string; access?: boolean; include: string[] }
      ) =>
        deploy(
          directory,
          cmdOptions.subdomain,
          program.opts(),
          deploymentAccessPatterns(cmdOptions)
        )
    );
}
