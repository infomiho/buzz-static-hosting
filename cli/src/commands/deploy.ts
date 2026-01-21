import { Command } from "commander";
import { existsSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { getOptions, authHeaders, createZipBuffer } from "../lib.js";

export async function deploy(directory: string, subdomain: string | undefined) {
  const options = getOptions();

  if (!options.token) {
    console.error("Error: Not authenticated. Run 'buzz login' first");
    process.exit(1);
  }

  const stat = statSync(directory);
  if (!stat.isDirectory()) {
    console.error(`Error: '${directory}' is not a directory`);
    process.exit(1);
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

  console.log(`Zipping ${directory}...`);
  const zipBuffer = await createZipBuffer(directory);

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

  try {
    const response = await fetch(`${options.server}/deploy`, {
      method: "POST",
      headers,
      body,
    });

    const data = await response.json();

    if (response.ok) {
      console.log(`Deployed to ${data.url}`);
      // Save subdomain to CNAME file in cwd
      const deployedSubdomain = new URL(data.url).hostname.split(".")[0];
      writeFileSync(cwdCnamePath, deployedSubdomain + "\n");
    } else if (response.status === 401) {
      console.error("Error: Not authenticated. Run 'buzz login' first");
      process.exit(1);
    } else if (response.status === 403) {
      console.error(`Error: ${data.error}`);
      if (data.error?.includes("owned by another user")) {
        console.error("Tip: Choose a different subdomain with --subdomain <name>");
      }
      process.exit(1);
    } else {
      console.error(`Error: ${data.error || "Unknown error"}`);
      process.exit(1);
    }
  } catch (error) {
    console.error(
      `Error: Could not connect to server - ${error instanceof Error ? error.message : error}`
    );
    process.exit(1);
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
