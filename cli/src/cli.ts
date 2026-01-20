import { program } from "commander";
import { existsSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const CONFIG_PATH = join(homedir(), ".buzz.config.json");
const DEFAULT_SERVER = "http://localhost:8080";

interface Config {
  server?: string;
  token?: string;  // Session token from login
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

interface DeploymentToken {
  id: string;
  name: string;
  site_name: string;
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
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
    // Priority: CLI flag > env var > config file
    server: opts.server || process.env.BUZZ_SERVER || config.server || DEFAULT_SERVER,
    token: opts.token || process.env.BUZZ_TOKEN || config.token,
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

class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public code?: string
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function apiRequest(
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

async function list() {
  try {
    const response = await apiRequest("/sites");
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
    if (error instanceof ApiError) {
      console.error(`Error: ${error.message}`);
      process.exit(1);
    }
    throw error;
  }
}

async function deleteSite(subdomain: string) {
  try {
    const response = await apiRequest(`/sites/${subdomain}`, { method: "DELETE" });

    if (response.status === 204) {
      console.log(`Deleted ${subdomain}`);
    } else if (response.status === 404) {
      console.error(`Error: Site '${subdomain}' not found`);
      process.exit(1);
    } else {
      const data = await response.json();
      console.error(`Error: ${data.error || "Unknown error"}`);
      process.exit(1);
    }
  } catch (error) {
    if (error instanceof ApiError) {
      console.error(`Error: ${error.message}`);
      process.exit(1);
    }
    throw error;
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
      return;
    }
    console.log("Current configuration:");
    if (config.server) console.log(`  server: ${config.server}`);
    if (config.token) console.log(`  token: ${config.token.slice(0, 16)}...`);
    console.log(`\nConfig file: ${CONFIG_PATH}`);
    return;
  }

  if (key === "server" && value) {
    config.server = value;
    saveConfig(config);
    console.log(`Server set to ${value}`);
  } else {
    console.error("Usage: buzz config server <url>");
    console.error("Use 'buzz login' to authenticate");
    process.exit(1);
  }
}

async function login() {
  const options = getOptions();

  try {
    // Start device flow
    const deviceResponse = await fetch(`${options.server}/auth/device`, {
      method: "POST",
    });

    if (!deviceResponse.ok) {
      const data = await deviceResponse.json();
      console.error(`Error: ${data.error || "Failed to start login"}`);
      process.exit(1);
    }

    const deviceData = await deviceResponse.json();

    console.log(`\nVisit: ${deviceData.verification_uri}`);
    console.log(`Enter code: ${deviceData.user_code}\n`);
    console.log("Waiting for authorization...");

    // Poll for completion
    const interval = (deviceData.interval || 5) * 1000;
    const maxAttempts = Math.ceil((deviceData.expires_in || 900) / (deviceData.interval || 5));

    for (let i = 0; i < maxAttempts; i++) {
      await new Promise((resolve) => setTimeout(resolve, interval));

      const pollResponse = await fetch(`${options.server}/auth/device/poll`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_code: deviceData.device_code }),
      });

      const pollData = await pollResponse.json();

      if (pollData.status === "pending") {
        continue;
      }

      if (pollData.error) {
        console.error(`\nError: ${pollData.error}`);
        process.exit(1);
      }

      if (pollData.status === "complete") {
        // Save token
        const config = loadConfig();
        config.token = pollData.token;
        saveConfig(config);
        console.log(`\nLogged in as ${pollData.user.login}`);
        return;
      }
    }

    console.error("\nLogin timed out");
    process.exit(1);
  } catch (error) {
    console.error(
      `Error: Could not connect to server - ${error instanceof Error ? error.message : error}`
    );
    process.exit(1);
  }
}

async function logout() {
  const options = getOptions();

  if (!options.token) {
    console.log("Not logged in");
    return;
  }

  try {
    await fetch(`${options.server}/auth/logout`, {
      method: "POST",
      headers: authHeaders(options.token),
    });
  } catch {
    // Ignore errors - we're logging out anyway
  }

  // Clear token from config
  const config = loadConfig();
  delete config.token;
  saveConfig(config);
  console.log("Logged out");
}

async function whoami() {
  try {
    const response = await apiRequest("/auth/me");
    const user = await response.json();
    console.log(`Logged in as ${user.login}${user.name ? ` (${user.name})` : ""}`);
  } catch (error) {
    if (error instanceof ApiError) {
      console.error(`Error: ${error.message}`);
      process.exit(1);
    }
    throw error;
  }
}

async function listTokens() {
  try {
    const response = await apiRequest("/tokens");
    const tokens: DeploymentToken[] = await response.json();

    if (tokens.length === 0) {
      console.log("No deployment tokens");
      return;
    }

    console.log(
      `${"ID".padEnd(18)} ${"NAME".padEnd(20)} ${"SITE".padEnd(20)} ${"LAST USED".padEnd(20)}`
    );
    for (const token of tokens) {
      const lastUsed = token.last_used_at
        ? token.last_used_at.slice(0, 19).replace("T", " ")
        : "Never";
      console.log(
        `${token.id.padEnd(18)} ${token.name.slice(0, 18).padEnd(20)} ${token.site_name.slice(0, 18).padEnd(20)} ${lastUsed.padEnd(20)}`
      );
    }
  } catch (error) {
    if (error instanceof ApiError) {
      console.error(`Error: ${error.message}`);
      process.exit(1);
    }
    throw error;
  }
}

async function createToken(siteName: string, cmdOptions: { name?: string }) {
  try {
    const response = await apiRequest("/tokens", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        site_name: siteName,
        name: cmdOptions.name || "Deployment token",
      }),
    });

    if (response.status === 404) {
      console.error(`Error: Site '${siteName}' not found`);
      process.exit(1);
    }

    if (!response.ok) {
      const data = await response.json();
      console.error(`Error: ${data.error || "Unknown error"}`);
      process.exit(1);
    }

    const data = await response.json();
    console.log(`Token created for site '${siteName}':\n`);
    console.log(`  ${data.token}\n`);
    console.log("Save this token - it won't be shown again!");
    console.log("\nUse in CI by setting BUZZ_TOKEN environment variable.");
  } catch (error) {
    if (error instanceof ApiError) {
      console.error(`Error: ${error.message}`);
      process.exit(1);
    }
    throw error;
  }
}

async function deleteToken(tokenId: string) {
  try {
    const response = await apiRequest(`/tokens/${tokenId}`, { method: "DELETE" });

    if (response.status === 204) {
      console.log("Token deleted");
      return;
    }

    if (response.status === 404) {
      console.error("Token not found");
      process.exit(1);
    }

    const data = await response.json();
    console.error(`Error: ${data.error || "Unknown error"}`);
    process.exit(1);
  } catch (error) {
    if (error instanceof ApiError) {
      console.error(`Error: ${error.message}`);
      process.exit(1);
    }
    throw error;
  }
}

program
  .name("buzz")
  .description("CLI for deploying static sites to Buzz hosting")
  .version("1.0.0")
  .option("-s, --server <url>", "Server URL (overrides config)")
  .option("-t, --token <token>", "Auth token (overrides config)");

program
  .command("deploy <directory>")
  .description("Deploy a directory to the server")
  .option("--subdomain <name>", "Subdomain for the site")
  .action((directory: string, cmdOptions: { subdomain?: string }) => deploy(directory, cmdOptions.subdomain));

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
  .description("View or set configuration (server)")
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

// Auth commands
program
  .command("login")
  .description("Login with GitHub OAuth")
  .action(login);

program
  .command("logout")
  .description("Logout and clear session")
  .action(logout);

program
  .command("whoami")
  .description("Show current logged-in user")
  .action(whoami);

// Token commands
const tokensCmd = program
  .command("tokens")
  .description("Manage deployment tokens");

tokensCmd
  .command("list")
  .description("List your deployment tokens")
  .action(listTokens);

tokensCmd
  .command("create <site>")
  .description("Create a deployment token for a site")
  .option("-n, --name <name>", "Token name (for identification)")
  .action(createToken);

tokensCmd
  .command("delete <token-id>")
  .description("Delete a deployment token")
  .action(deleteToken);

program.parse();
