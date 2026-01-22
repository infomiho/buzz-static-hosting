import { Command } from "commander";
import { apiRequest, CliError, DeploymentToken } from "../lib.js";

export async function listTokens() {
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
}

export async function createToken(siteName: string, cmdOptions: { name?: string }) {
  const response = await apiRequest("/tokens", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      site_name: siteName,
      name: cmdOptions.name || "Deployment token",
    }),
  });

  if (response.status === 404) {
    throw new CliError(`Site '${siteName}' not found`);
  }

  if (!response.ok) {
    const data = await response.json();
    throw new CliError(data.error || "Unknown error");
  }

  const data = await response.json();
  console.log(`Token created for site '${siteName}':\n`);
  console.log(`  ${data.token}\n`);
  console.log("Save this token - it won't be shown again!");
  console.log("\nUse in CI by setting BUZZ_TOKEN environment variable.");
}

export async function deleteToken(tokenId: string) {
  const response = await apiRequest(`/tokens/${tokenId}`, { method: "DELETE" });

  if (response.status === 204) {
    console.log("Token deleted");
    return;
  }

  if (response.status === 404) {
    throw new CliError("Token not found");
  }

  const data = await response.json();
  throw new CliError(data.error || "Unknown error");
}

export function registerTokensCommand(program: Command) {
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
}
