import { describe, it, expect } from "vitest";
import { mkdtempSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { resolveSubdomain, packSite, uploadSite } from "./deploy.js";

function makeTmpDir(): string {
  return mkdtempSync(join(tmpdir(), "buzz-test-"));
}

describe("resolveSubdomain", () => {
  it("returns explicit arg when provided", () => {
    const cwd = makeTmpDir();
    const dir = makeTmpDir();
    writeFileSync(join(cwd, "CNAME"), "from-cwd\n");

    expect(resolveSubdomain(cwd, dir, "explicit")).toBe("explicit");
  });

  it("falls back to cwd CNAME", () => {
    const cwd = makeTmpDir();
    const dir = makeTmpDir();
    writeFileSync(join(cwd, "CNAME"), "from-cwd\n");

    expect(resolveSubdomain(cwd, dir)).toBe("from-cwd");
  });

  it("falls back to directory CNAME when no cwd CNAME", () => {
    const cwd = makeTmpDir();
    const dir = makeTmpDir();
    writeFileSync(join(dir, "CNAME"), "from-dir\n");

    expect(resolveSubdomain(cwd, dir)).toBe("from-dir");
  });

  it("prefers cwd CNAME over directory CNAME", () => {
    const cwd = makeTmpDir();
    const dir = makeTmpDir();
    writeFileSync(join(cwd, "CNAME"), "from-cwd\n");
    writeFileSync(join(dir, "CNAME"), "from-dir\n");

    expect(resolveSubdomain(cwd, dir)).toBe("from-cwd");
  });

  it("returns undefined when no CNAME exists", () => {
    const cwd = makeTmpDir();
    const dir = makeTmpDir();

    expect(resolveSubdomain(cwd, dir)).toBeUndefined();
  });
});

describe("packSite", () => {
  it("returns a valid ZIP buffer", async () => {
    const dir = makeTmpDir();
    writeFileSync(join(dir, "index.html"), "<h1>hello</h1>");
    writeFileSync(join(dir, "style.css"), "body {}");

    const buffer = await packSite(dir);

    expect(buffer).toBeInstanceOf(Buffer);
    expect(buffer.length).toBeGreaterThan(0);
    // ZIP magic bytes
    expect(buffer[0]).toBe(0x50);
    expect(buffer[1]).toBe(0x4b);
  });

  it("throws for nonexistent directory", async () => {
    await expect(packSite("/tmp/does-not-exist-xyz")).rejects.toThrow(
      "does not exist"
    );
  });

  it("throws for a file instead of directory", async () => {
    const dir = makeTmpDir();
    const file = join(dir, "not-a-dir.txt");
    writeFileSync(file, "hello");

    await expect(packSite(file)).rejects.toThrow("is not a directory");
  });

  it("calls onProgress callback", async () => {
    const dir = makeTmpDir();
    writeFileSync(join(dir, "a.txt"), "aaa");
    writeFileSync(join(dir, "b.txt"), "bbb");

    const calls: Array<[number, number]> = [];
    await packSite(dir, (processed, total) => {
      calls.push([processed, total]);
    });

    expect(calls.length).toBeGreaterThan(0);
  });
});

function fakeFetch(status: number, body: object): typeof fetch {
  return async () =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
}

describe("uploadSite", () => {
  const zip = Buffer.from("fake-zip-content");

  it("returns url and subdomain on success", async () => {
    const result = await uploadSite(
      "http://localhost:8080",
      "test-token",
      zip,
      "my-site",
      fakeFetch(200, { url: "https://my-site.example.com" })
    );

    expect(result.url).toBe("https://my-site.example.com");
    expect(result.subdomain).toBe("my-site");
  });

  it("throws CliError on 401", async () => {
    await expect(
      uploadSite(
        "http://localhost:8080",
        "bad-token",
        zip,
        undefined,
        fakeFetch(401, { detail: "Unauthorized" })
      )
    ).rejects.toThrow("Not authenticated");
  });

  it("throws CliError on 403", async () => {
    await expect(
      uploadSite(
        "http://localhost:8080",
        "test-token",
        zip,
        "taken",
        fakeFetch(403, { detail: "Site 'taken' is owned by another user" })
      )
    ).rejects.toThrow("owned by another user");
  });

  it("includes tip for ownership errors", async () => {
    try {
      await uploadSite(
        "http://localhost:8080",
        "test-token",
        zip,
        "taken",
        fakeFetch(403, { detail: "Site 'taken' is owned by another user" })
      );
      expect.unreachable();
    } catch (error: any) {
      expect(error.tip).toBe(
        "Choose a different subdomain with --subdomain <name>"
      );
    }
  });

  it("throws CliError on connection failure", async () => {
    const failingFetch: typeof fetch = async () => {
      throw new Error("ECONNREFUSED");
    };

    await expect(
      uploadSite(
        "http://localhost:9999",
        "test-token",
        zip,
        undefined,
        failingFetch
      )
    ).rejects.toThrow("Could not connect to server");
  });

  it("throws CliError on unknown server error", async () => {
    await expect(
      uploadSite(
        "http://localhost:8080",
        "test-token",
        zip,
        undefined,
        fakeFetch(500, { detail: "Internal server error" })
      )
    ).rejects.toThrow("Internal server error");
  });
});
