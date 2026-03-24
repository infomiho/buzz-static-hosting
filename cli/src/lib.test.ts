import { describe, it, expect } from "vitest";
import { formatSize, authHeaders } from "./lib.js";

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
