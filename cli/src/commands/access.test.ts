import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { accessStatus, makePrivate, makePublic } from "./access.js";
import { createProgram } from "../program.js";

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

  it("makes the site inferred from CNAME private", async () => {
    const directory = mkdtempSync(join(tmpdir(), "buzz-access-test-"));
    writeFileSync(join(directory, "CNAME"), "my-site\n");
    process.chdir(directory);
    fetchMock.mockResolvedValueOnce(jsonResponse({ private: true }));

    await makePrivate({}, cliOptions);

    expect(fetchMock).toHaveBeenCalledWith(
      "https://buzz.example.com/sites/my-site/access",
      expect.objectContaining({ method: "PUT" })
    );
    expect(console.log).toHaveBeenCalledWith("my-site: private");
  });

  it("makes an explicitly named site private", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ private: true }));

    await makePrivate({ site: "other-site" }, cliOptions);

    expect(fetchMock.mock.calls[0][0]).toBe(
      "https://buzz.example.com/sites/other-site/access"
    );
    expect(console.log).toHaveBeenCalledWith("other-site: private");
  });

  it.each([
    [{ private: false }, "my-site: public"],
    [{ private: true }, "my-site: private"],
  ])("prints the access status", async (state, output) => {
    fetchMock.mockResolvedValueOnce(jsonResponse(state));

    await accessStatus({ site: "my-site" }, cliOptions);

    expect(fetchMock.mock.calls[0][1]?.method).toBeUndefined();
    expect(console.log).toHaveBeenCalledWith(output);
  });

  it("does not make a site public without confirmation", async () => {
    const confirm = vi.fn().mockResolvedValue(false);

    await makePublic({ site: "my-site" }, cliOptions, { confirm });

    expect(confirm).toHaveBeenCalledWith("Make 'my-site' public?");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(console.log).toHaveBeenCalledWith("Aborted.");
  });

  it("makes a site public without prompting when --yes is set", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    const confirm = vi.fn();

    await makePublic({ site: "my-site", yes: true }, cliOptions, { confirm });

    expect(confirm).not.toHaveBeenCalled();
    expect(fetchMock.mock.calls[0][1]?.method).toBe("DELETE");
    expect(console.log).toHaveBeenCalledWith("my-site: public");
  });

  // Driving real argv, because --site is the difference between changing the
  // site you named and changing whichever site the current directory points at.
  it.each([
    [["access", "--site", "named-site"], "GET"],
    [["access", "private", "--site", "named-site"], "PUT"],
    [["access", "public", "--site", "named-site", "-y"], "DELETE"],
  ])("routes %j to the named site", async (argv, method) => {
    const directory = mkdtempSync(join(tmpdir(), "buzz-access-test-"));
    writeFileSync(join(directory, "CNAME"), "cname-site\n");
    process.chdir(directory);
    fetchMock.mockResolvedValue(
      method === "DELETE"
        ? new Response(null, { status: 204 })
        : jsonResponse({ private: true })
    );

    await createProgram().parseAsync([
      "node",
      "buzz",
      "--server",
      "https://buzz.example.com",
      "--token",
      "session-token",
      ...argv,
    ]);

    expect(fetchMock.mock.calls[0][0]).toBe(
      "https://buzz.example.com/sites/named-site/access"
    );
    expect(fetchMock.mock.calls[0][1]?.method ?? "GET").toBe(method);
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
