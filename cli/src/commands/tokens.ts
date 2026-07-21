import { Command } from "commander";
import {
  isRecord,
  requestEmpty,
  requestJson,
  type CliOptions,
} from "../client.js";

interface DeploymentToken {
  id: string;
  name: string;
  site_name: string;
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
}

function isDeploymentTokenArray(value: unknown): value is DeploymentToken[] {
  return (
    Array.isArray(value) &&
    value.every(
      (token) =>
        isRecord(token) &&
        typeof token.id === "string" &&
        typeof token.name === "string" &&
        typeof token.site_name === "string" &&
        (token.last_used_at === null || typeof token.last_used_at === "string")
    )
  );
}

interface CreatedToken {
  token: string;
}

function isCreatedToken(value: unknown): value is CreatedToken {
  return isRecord(value) && typeof value.token === "string";
}

export async function listTokens(cliOptions: CliOptions = {}) {
  const tokens = await requestJson(
    "/tokens",
    {
      guard: isDeploymentTokenArray,
      invalid: "Server returned an invalid deployment-token response",
    },
    {},
    { cliOptions }
  );

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
}

export async function createToken(
  siteName: string,
  cmdOptions: { name?: string },
  cliOptions: CliOptions = {}
) {
  const data = await requestJson(
    "/tokens",
    {
      guard: isCreatedToken,
      invalid: "Server returned an invalid deployment-token response",
    },
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        site_name: siteName,
        name: cmdOptions.name || "Deployment token",
      }),
    },
    {
      cliOptions,
      errors: { notFound: `Site '${siteName}' not found`, fallback: "Unknown error" },
    }
  );

  console.log(`Token created for site '${siteName}':\n`);
  console.log(`  ${data.token}\n`);
  console.log("Save this token - it won't be shown again!");
  console.log("\nUse in CI by setting BUZZ_TOKEN environment variable.");
}

export async function deleteToken(
  tokenId: string,
  cliOptions: CliOptions = {}
) {
  await requestEmpty(
    `/tokens/${tokenId}`,
    [204],
    { method: "DELETE" },
    { cliOptions, errors: { notFound: "Token not found", fallback: "Unknown error" } }
  );
  console.log("Token deleted");
}

export function registerTokensCommand(program: Command) {
  const tokensCmd = program
    .command("tokens")
    .description("Manage deployment tokens");

  tokensCmd
    .command("list")
    .description("List your deployment tokens")
    .action(() => listTokens(program.opts()));

  tokensCmd
    .command("create <site>")
    .description("Create a deployment token for a site")
    .option("-n, --name <name>", "Token name (for identification)")
    .action((site: string, options: { name?: string }) =>
      createToken(site, options, program.opts())
    );

  tokensCmd
    .command("delete <token-id>")
    .description("Delete a deployment token")
    .action((tokenId: string) => deleteToken(tokenId, program.opts()));
}
