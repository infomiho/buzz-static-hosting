import { handleError } from "./lib.js";
import { createProgram } from "./program.js";

async function main() {
  await createProgram().parseAsync();
}

main().catch(handleError);
