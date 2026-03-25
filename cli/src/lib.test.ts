import { describe, it, expect } from "vitest";
import { mkdtempSync, writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import JSZip from "jszip";
import { formatSize, authHeaders, createZipBuffer } from "./lib.js";

describe("formatSize", () => {
  it("formats bytes", () => {
    expect(formatSize(500)).toBe("500 B");
  });

  it("formats kilobytes", () => {
    expect(formatSize(2048)).toBe("2.0 KB");
  });

  it("formats megabytes", () => {
    expect(formatSize(3 * 1024 * 1024)).toBe("3.0 MB");
  });
});

describe("authHeaders", () => {
  it("returns bearer header when token provided", () => {
    expect(authHeaders("my-token")).toEqual({
      Authorization: "Bearer my-token",
    });
  });

  it("returns empty object when no token", () => {
    expect(authHeaders()).toEqual({});
  });
});

function makeTmpDir(): string {
  return mkdtempSync(join(tmpdir(), "buzz-zip-test-"));
}

async function zipEntries(buf: Buffer): Promise<string[]> {
  const zip = await JSZip.loadAsync(buf);
  return Object.keys(zip.files).filter((f) => !f.endsWith("/")).sort();
}

describe("createZipBuffer", () => {
  it("includes normal files", async () => {
    const dir = makeTmpDir();
    writeFileSync(join(dir, "index.html"), "<h1>hi</h1>");
    writeFileSync(join(dir, "style.css"), "body{}");

    const buf = await createZipBuffer(dir);
    const entries = await zipEntries(buf);

    expect(entries).toContain("index.html");
    expect(entries).toContain("style.css");
  });

  it("excludes .git directory", async () => {
    const dir = makeTmpDir();
    writeFileSync(join(dir, "index.html"), "hi");
    mkdirSync(join(dir, ".git"));
    writeFileSync(join(dir, ".git", "config"), "gitconfig");
    writeFileSync(join(dir, ".git", "HEAD"), "ref: refs/heads/main");

    const buf = await createZipBuffer(dir);
    const entries = await zipEntries(buf);

    expect(entries).toContain("index.html");
    expect(entries).not.toContain(".git/config");
    expect(entries).not.toContain(".git/HEAD");
  });

  it("excludes .DS_Store files", async () => {
    const dir = makeTmpDir();
    writeFileSync(join(dir, "index.html"), "hi");
    writeFileSync(join(dir, ".DS_Store"), "");

    const buf = await createZipBuffer(dir);
    const entries = await zipEntries(buf);

    expect(entries).toContain("index.html");
    expect(entries).not.toContain(".DS_Store");
  });

  it("excludes .env and .env.* files", async () => {
    const dir = makeTmpDir();
    writeFileSync(join(dir, "index.html"), "hi");
    writeFileSync(join(dir, ".env"), "SECRET=123");
    writeFileSync(join(dir, ".env.local"), "SECRET=456");
    writeFileSync(join(dir, ".env.production"), "SECRET=789");

    const buf = await createZipBuffer(dir);
    const entries = await zipEntries(buf);

    expect(entries).toContain("index.html");
    expect(entries).not.toContain(".env");
    expect(entries).not.toContain(".env.local");
    expect(entries).not.toContain(".env.production");
  });

  it("excludes .vscode and .idea directories", async () => {
    const dir = makeTmpDir();
    writeFileSync(join(dir, "index.html"), "hi");
    mkdirSync(join(dir, ".vscode"));
    writeFileSync(join(dir, ".vscode", "settings.json"), "{}");
    mkdirSync(join(dir, ".idea"));
    writeFileSync(join(dir, ".idea", "workspace.xml"), "<xml/>");

    const buf = await createZipBuffer(dir);
    const entries = await zipEntries(buf);

    expect(entries).toContain("index.html");
    expect(entries).not.toContain(".vscode/settings.json");
    expect(entries).not.toContain(".idea/workspace.xml");
  });

  it("excludes node_modules directory", async () => {
    const dir = makeTmpDir();
    writeFileSync(join(dir, "index.html"), "hi");
    mkdirSync(join(dir, "node_modules"));
    mkdirSync(join(dir, "node_modules", "some-pkg"));
    writeFileSync(join(dir, "node_modules", "some-pkg", "index.js"), "module.exports = {}");

    const buf = await createZipBuffer(dir);
    const entries = await zipEntries(buf);

    expect(entries).toContain("index.html");
    expect(entries).not.toContain("node_modules/some-pkg/index.js");
  });

  it("includes .well-known directory", async () => {
    const dir = makeTmpDir();
    writeFileSync(join(dir, "index.html"), "hi");
    mkdirSync(join(dir, ".well-known"));
    writeFileSync(join(dir, ".well-known", "acme-challenge"), "token123");

    const buf = await createZipBuffer(dir);
    const entries = await zipEntries(buf);

    expect(entries).toContain("index.html");
    expect(entries).toContain(".well-known/acme-challenge");
  });

  it("excludes nested .DS_Store files", async () => {
    const dir = makeTmpDir();
    mkdirSync(join(dir, "assets"));
    writeFileSync(join(dir, "assets", "logo.png"), "img");
    writeFileSync(join(dir, "assets", ".DS_Store"), "");

    const buf = await createZipBuffer(dir);
    const entries = await zipEntries(buf);

    expect(entries).toContain("assets/logo.png");
    expect(entries).not.toContain("assets/.DS_Store");
  });
});
