import { createInterface } from "node:readline/promises";

export async function confirm(message: string): Promise<boolean> {
  if (!process.stdin.isTTY || !process.stdout.isTTY) {
    return false;
  }
  const prompt = createInterface({ input: process.stdin, output: process.stdout });
  try {
    const answer = await prompt.question(`${message} [y/N] `);
    return ["y", "yes"].includes(answer.trim().toLowerCase());
  } finally {
    prompt.close();
  }
}
