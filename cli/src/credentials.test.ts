import { describe, it, expect } from "vitest";
import { chmodSync, mkdtempSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { tmpdir } from "node:os";
import {
  DEFAULT_SERVER,
  clearCredential,
  getCredential,
  loadConfig,
  normalizeServerUrl,
  saveConfig,
  setCredential,
  type Config,
} from "./credentials.js";
import { CliError } from "./errors.js";

function tmpConfigPath(): string {
  return join(mkdtempSync(join(tmpdir(), "buzz-config-test-")), "config.json");
}

describe("normalizeServerUrl", () => {
  it("lowercases the host and strips trailing slashes", () => {
    expect(normalizeServerUrl("HTTPS://Buzz.Example.COM/")).toBe("https://buzz.example.com");
  });

  it("drops default ports", () => {
    expect(normalizeServerUrl("https://buzz.example.com:443")).toBe("https://buzz.example.com");
    expect(normalizeServerUrl("http://buzz.example.com:80")).toBe("http://buzz.example.com");
  });

  it("keeps custom ports", () => {
    expect(normalizeServerUrl("http://localhost:8080")).toBe("http://localhost:8080");
  });

  it("drops userinfo, query, and fragment", () => {
    expect(normalizeServerUrl("https://user:pass@buzz.example.com/?q=1#top")).toBe(
      "https://buzz.example.com"
    );
  });

  it("rejects non-http schemes", () => {
    expect(() => normalizeServerUrl("ftp://buzz.example.com")).toThrow(CliError);
  });

  it("rejects strings that are not URLs", () => {
    expect(() => normalizeServerUrl("buzz.example.com")).toThrow(CliError);
  });
});

describe("credential scoping", () => {
  it("returns the token stored for the same server", () => {
    const config: Config = {};
    setCredential(config, "https://buzz.example.com", "token-a");

    expect(getCredential(config, "https://buzz.example.com")).toBe("token-a");
  });

  it("returns nothing for a different server", () => {
    const config: Config = {};
    setCredential(config, "https://a.example.com", "token-a");

    expect(getCredential(config, "https://b.example.com")).toBeUndefined();
  });

  it("matches equivalent spellings of the same server", () => {
    const config: Config = {};
    setCredential(config, "HTTPS://Buzz.Example.COM:443/", "token-a");

    expect(getCredential(config, "https://buzz.example.com")).toBe("token-a");
  });

  it("clears only the credential for the given server", () => {
    const config: Config = {};
    setCredential(config, "https://a.example.com", "token-a");
    setCredential(config, "https://b.example.com", "token-b");

    clearCredential(config, "https://a.example.com");

    expect(getCredential(config, "https://a.example.com")).toBeUndefined();
    expect(getCredential(config, "https://b.example.com")).toBe("token-b");
  });
});

describe("loadConfig", () => {
  it("returns an empty config when the file is missing", () => {
    expect(loadConfig(tmpConfigPath())).toEqual({});
  });

  it("round-trips a saved config", () => {
    const path = tmpConfigPath();
    const config: Config = { server: "https://buzz.example.com" };
    setCredential(config, "https://buzz.example.com", "token-a");

    saveConfig(config, path);

    expect(loadConfig(path)).toEqual(config);
  });

  it("migrates a legacy token to the configured server", () => {
    const path = tmpConfigPath();
    writeFileSync(path, JSON.stringify({ server: "https://buzz.example.com/", token: "legacy" }));

    const config = loadConfig(path);

    expect(getCredential(config, "https://buzz.example.com")).toBe("legacy");
    expect(getCredential(config, "https://other.example.com")).toBeUndefined();
  });

  it("migrates a legacy token to the default server when none is configured", () => {
    const path = tmpConfigPath();
    writeFileSync(path, JSON.stringify({ token: "legacy" }));

    const config = loadConfig(path);

    expect(getCredential(config, DEFAULT_SERVER)).toBe("legacy");
  });

  it("migrates a legacy token to BUZZ_SERVER when no server is configured", () => {
    const path = tmpConfigPath();
    writeFileSync(path, JSON.stringify({ token: "legacy" }));

    process.env.BUZZ_SERVER = "https://env.example.com";
    try {
      const config = loadConfig(path);

      expect(getCredential(config, "https://env.example.com")).toBe("legacy");
      expect(getCredential(config, DEFAULT_SERVER)).toBeUndefined();
    } finally {
      delete process.env.BUZZ_SERVER;
    }
  });

  it("prefers the configured server over BUZZ_SERVER for legacy migration", () => {
    const path = tmpConfigPath();
    writeFileSync(path, JSON.stringify({ server: "https://buzz.example.com", token: "legacy" }));

    process.env.BUZZ_SERVER = "https://env.example.com";
    try {
      const config = loadConfig(path);

      expect(getCredential(config, "https://buzz.example.com")).toBe("legacy");
      expect(getCredential(config, "https://env.example.com")).toBeUndefined();
    } finally {
      delete process.env.BUZZ_SERVER;
    }
  });

  it("prefers a scoped credential over a legacy token for the same server", () => {
    const path = tmpConfigPath();
    writeFileSync(
      path,
      JSON.stringify({
        server: "https://buzz.example.com",
        token: "legacy",
        credentials: { "https://buzz.example.com": "scoped" },
      })
    );

    expect(getCredential(loadConfig(path), "https://buzz.example.com")).toBe("scoped");
  });

  it("returns an empty config for corrupt JSON", () => {
    const path = tmpConfigPath();
    writeFileSync(path, "{not json");

    expect(loadConfig(path)).toEqual({});
  });

  it("ignores entries that are not usable", () => {
    const path = tmpConfigPath();
    writeFileSync(
      path,
      JSON.stringify({
        server: 42,
        credentials: {
          "https://buzz.example.com": "token-a",
          "not a url": "token-b",
          "https://other.example.com": 7,
        },
      })
    );

    const config = loadConfig(path);

    expect(config.server).toBeUndefined();
    expect(getCredential(config, "https://buzz.example.com")).toBe("token-a");
    expect(Object.keys(config.credentials ?? {})).toEqual(["https://buzz.example.com"]);
  });
});

describe("saveConfig", () => {
  it("creates the file readable only by the owner", () => {
    const path = tmpConfigPath();

    saveConfig({ server: "https://buzz.example.com" }, path);

    expect(statSync(path).mode & 0o777).toBe(0o600);
  });

  it("tightens permissions when overwriting a world-readable file", () => {
    const path = tmpConfigPath();
    writeFileSync(path, "{}");
    chmodSync(path, 0o644);

    saveConfig({ server: "https://buzz.example.com" }, path);

    expect(statSync(path).mode & 0o777).toBe(0o600);
  });

  it("leaves no temporary files behind", () => {
    const path = tmpConfigPath();

    saveConfig({ server: "https://buzz.example.com" }, path);

    expect(readdirSync(dirname(path))).toEqual(["config.json"]);
  });
});
