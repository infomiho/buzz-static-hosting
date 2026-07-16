import { afterEach, describe, expect, it, vi } from "vitest";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { url } from "./url.js";

describe("url", () => {
  afterEach(() => vi.restoreAllMocks());

  it("always prints the canonical Buzz hostname from CNAME", () => {
    const directory = mkdtempSync(join(tmpdir(), "buzz-url-test-"));
    writeFileSync(join(directory, "CNAME"), "my-site\n");
    vi.spyOn(process, "cwd").mockReturnValue(directory);
    const output = vi.spyOn(console, "log").mockImplementation(() => undefined);

    url({ server: "https://buzz.example.com", token: "session-token" });

    expect(output).toHaveBeenCalledWith("https://my-site.buzz.example.com");
  });
});
