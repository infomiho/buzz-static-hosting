import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { accessStatus, disableAccess, enableAccess } from "./access.js";

const cliOptions = { server: "https://buzz.example.com", token: "session-token" };

function jsonResponse(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("access commands", () => {
  const fetchMock = vi.fn<typeof fetch>();
  const originalCwd = process.cwd();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(console, "log").mockImplementation(() => undefined);
  });

  afterEach(() => {
    process.chdir(originalCwd);
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("enables the entire site inferred from CNAME", async () => {
    const directory = mkdtempSync(join(tmpdir(), "buzz-access-test-"));
    writeFileSync(join(directory, "CNAME"), "my-site\n");
    process.chdir(directory);
    fetchMock.mockResolvedValueOnce(jsonResponse({ enabled: true, patterns: ["/"] }));

    await enableAccess({ include: [] }, cliOptions);

    expect(fetchMock).toHaveBeenCalledWith(
      "https://buzz.example.com/sites/my-site/access",
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({ patterns: ["/"] }),
      })
    );
    expect(console.log).toHaveBeenCalledWith("my-site: entire site");
  });

  it("preserves repeated include patterns and uses an explicit site", async () => {
    const patterns = ["/admin/*", "/drafts/**", "/admin/*"];
    fetchMock.mockResolvedValueOnce(jsonResponse({ enabled: true, patterns }));

    await enableAccess({ site: "other-site", include: patterns }, cliOptions);

    expect(fetchMock.mock.calls[0][1]?.body).toBe(JSON.stringify({ patterns }));
    expect(console.log).toHaveBeenCalledWith("other-site:");
    expect(console.log).toHaveBeenCalledWith("  /drafts/**");
    expect(console.log).toHaveBeenCalledTimes(4);
  });

  it.each([
    [{ enabled: false, patterns: [] }, "my-site: off"],
    [{ enabled: true, patterns: ["/"] }, "my-site: entire site"],
  ])("prints the access status", async (state, output) => {
    fetchMock.mockResolvedValueOnce(jsonResponse(state));

    await accessStatus({ site: "my-site" }, cliOptions);

    expect(fetchMock.mock.calls[0][1]?.method).toBeUndefined();
    expect(console.log).toHaveBeenCalledWith(output);
  });

  it("does not disable access without confirmation", async () => {
    const confirm = vi.fn().mockResolvedValue(false);

    await disableAccess({ site: "my-site" }, cliOptions, { confirm });

    expect(confirm).toHaveBeenCalledWith(
      "Disable Buzz Access for 'my-site'? The site will be public."
    );
    expect(fetchMock).not.toHaveBeenCalled();
    expect(console.log).toHaveBeenCalledWith("Aborted.");
  });

  it("disables access without prompting when --yes is set", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    const confirm = vi.fn();

    await disableAccess({ site: "my-site", yes: true }, cliOptions, { confirm });

    expect(confirm).not.toHaveBeenCalled();
    expect(fetchMock.mock.calls[0][1]?.method).toBe("DELETE");
    expect(console.log).toHaveBeenCalledWith("my-site: off");
  });

  it("explains that access management requires a full session", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: "Deploy tokens cannot perform this operation" }, 403)
    );

    await expect(accessStatus({ site: "my-site" }, cliOptions)).rejects.toMatchObject({
      message: "Deployment tokens cannot manage site access",
      tip: "Run 'buzz login' and retry with a full session",
    });
  });
});
