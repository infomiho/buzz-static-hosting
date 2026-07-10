export class CliError extends Error {
  constructor(message: string, public tip?: string) {
    super(message);
    this.name = "CliError";
  }
}
