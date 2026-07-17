import { describe, expect, it } from "vitest";
import { createProgram } from "./program.js";

function commandPaths() {
  const program = createProgram();
  return program.commands.flatMap((command) => [
    command.name(),
    ...command.commands.map((child) => `${command.name()} ${child.name()}`),
  ]);
}

describe("createProgram", () => {
  it("creates independent command trees", () => {
    const first = createProgram();
    const second = createProgram();

    expect(first).not.toBe(second);
    expect(first.commands).not.toBe(second.commands);
    expect(first.helpInformation()).toBe(second.helpInformation());
  });

  it("registers every public command", () => {
    expect(commandPaths()).toEqual([
      "deploy",
      "list",
      "delete",
      "config",
      "url",
      "login",
      "logout",
      "whoami",
      "tokens",
      "tokens list",
      "tokens create",
      "tokens delete",
      "domains",
      "domains list",
      "domains add",
      "domains check",
      "domains retry",
      "domains cancel-transition",
      "domains remove",
    ]);
  });

  it("keeps root help suitable for generated reference", () => {
    const help = createProgram().helpInformation();

    expect(help).toContain("Usage: buzz [options] [command]");
    expect(help).toContain("Buzz server URL");
    expect(help).toContain("Session or deployment token");
    expect(help).toContain("deploy [options] <directory>");
  });
});
