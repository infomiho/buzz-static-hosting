import { describe, expect, it } from "vitest";
import type { Command } from "commander";
import { createProgram } from "./program.js";

function commandPaths() {
  const program = createProgram();
  const help = program.createHelp();
  // visibleCommands drops hidden subcommands but adds Commander's implicit
  // `help`, which is not part of the CLI surface we document.
  const documented = (command: Command) =>
    help.visibleCommands(command).filter((child) => child.name() !== "help");
  return documented(program).flatMap((command) => [
    command.name(),
    ...documented(command).map((child) => `${command.name()} ${child.name()}`),
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
      "url",
      "delete",
      "access",
      "access status",
      "access private",
      "access public",
      "domains",
      "domains list",
      "domains add",
      "domains check",
      "domains retry",
      "domains cancel-transition",
      "domains remove",
      "tokens",
      "tokens list",
      "tokens create",
      "tokens delete",
      "login",
      "logout",
      "whoami",
      "config",
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
