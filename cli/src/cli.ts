#!/usr/bin/env node
import { program } from "commander";
import { existsSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const CONFIG_PATH = join(homedir(), ".buzz.config.json");
const DEFAULT_SERVER = "http://localhost:8080";

interface Config {
  server?: string;
  token?: string;
}

interface Site {
  name: string;
  created: string;
  size_bytes: number;
}

interface Options {
  server: string;
  token?: string;
}

function loadConfig(): Config {
  if (existsSync(CONFIG_PATH)) {
    try {
      return JSON.parse(readFileSync(CONFIG_PATH, "utf-8"));
    } catch {
      return {};
    }
  }
  return {};
}

function saveConfig(config: Config): void {
  writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2) + "\n");
}

function getOptions(): Options {
  const config = loadConfig();
  const opts = program.opts();
  return {
    server: opts.server || config.server || DEFAULT_SERVER,
    token: opts.token || config.token,
  };
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

function authHeaders(token?: string): Record<string, string> {
  if (token) {
    return { Authorization: `Bearer ${token}` };
  }
  return {};
}

async function createZipBuffer(directory: string): Promise<Buffer> {
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

async function deploy(directory: string, subdomain: string | undefined) {
  const options = getOptions();

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

async function list() {
  const options = getOptions();

  try {
    const response = await fetch(`${options.server}/sites`, {
      headers: authHeaders(options.token),
    });

    if (response.status === 401) {
      console.error("Error: Unauthorized - check your token");
      process.exit(1);
    }

    const sites: Site[] = await response.json();

    if (sites.length === 0) {
      console.log("No sites deployed");
      return;
    }

    console.log(
      `${"NAME".padEnd(24)} ${"CREATED".padEnd(20)} ${"SIZE".padEnd(10)}`
    );
    for (const site of sites) {
      const created = site.created.slice(0, 19).replace("T", " ");
      console.log(
        `${site.name.padEnd(24)} ${created.padEnd(20)} ${formatSize(site.size_bytes).padEnd(10)}`
      );
    }
  } catch (error) {
    console.error(
      `Error: Could not connect to server - ${error instanceof Error ? error.message : error}`
    );
    process.exit(1);
  }
}

async function deleteSite(subdomain: string) {
  const options = getOptions();

  try {
    const response = await fetch(`${options.server}/sites/${subdomain}`, {
      method: "DELETE",
      headers: authHeaders(options.token),
    });

    if (response.status === 204) {
      console.log(`Deleted ${subdomain}`);
    } else if (response.status === 401) {
      console.error("Error: Unauthorized - check your token");
      process.exit(1);
    } else if (response.status === 404) {
      console.error(`Error: Site '${subdomain}' not found`);
      process.exit(1);
    } else {
      const data = await response.json();
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

function configCommand(key?: string, value?: string) {
  const config = loadConfig();

  if (!key) {
    // Show current config
    if (Object.keys(config).length === 0) {
      console.log("No configuration set");
      console.log(`\nConfig file: ${CONFIG_PATH}`);
      console.log("\nUsage:");
      console.log("  buzz config server <url>    Set server URL");
      console.log("  buzz config token <token>   Set auth token");
      return;
    }
    console.log("Current configuration:");
    if (config.server) console.log(`  server: ${config.server}`);
    if (config.token) console.log(`  token: ${config.token.slice(0, 8)}...`);
    console.log(`\nConfig file: ${CONFIG_PATH}`);
    return;
  }

  if (key === "server" && value) {
    config.server = value;
    saveConfig(config);
    console.log(`Server set to ${value}`);
  } else if (key === "token" && value) {
    config.token = value;
    saveConfig(config);
    console.log("Token saved");
  } else {
    console.error("Usage: buzz config <server|token> <value>");
    process.exit(1);
  }
}

program
  .name("buzz")
  .description("CLI for deploying static sites to Buzz hosting")
  .version("1.0.0")
  .option("-s, --server <url>", "Server URL (overrides config)")
  .option("-t, --token <token>", "Auth token (overrides config)");

program
  .command("deploy <directory> [subdomain]")
  .description("Deploy a directory to the server")
  .action(deploy);

program
  .command("list")
  .description("List all deployed sites")
  .action(list);

program
  .command("delete <subdomain>")
  .description("Delete a deployed site")
  .action(deleteSite);

program
  .command("config [key] [value]")
  .description("View or set configuration (server, token)")
  .action(configCommand);

program
  .command("url")
  .description("Show the URL for the current directory")
  .action(() => {
    const cnamePath = join(process.cwd(), "CNAME");
    if (!existsSync(cnamePath)) {
      console.error("No CNAME file found. Deploy first with: buzz deploy .");
      process.exit(1);
    }
    const subdomain = readFileSync(cnamePath, "utf-8").trim();
    const config = loadConfig();
    const server = config.server || DEFAULT_SERVER;
    try {
      const host = new URL(server).hostname;
      console.log(`https://${subdomain}.${host}`);
    } catch {
      console.log(`http://${subdomain}.localhost:8080`);
    }
  });

program.parse();
