import cliProgress from "cli-progress";
import { CliError } from "./errors.js";

export { CliError } from "./errors.js";

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

export function handleError(error: unknown): void {
  if (error instanceof CliError) {
    console.error(`Error: ${error.message}`);
    if (error.tip) {
      console.error(`Tip: ${error.tip}`);
    }
  } else if (error instanceof Error) {
    console.error(`Error: ${error.message}`);
  } else {
    console.error(`Error: ${String(error ?? "Unknown error")}`);
  }
  process.exitCode = 1;
}

interface ProgressCallbacks {
  onProgress?: (processed: number, total: number) => void;
}

const IGNORED_DIRS = [".git", "node_modules", ".vscode", ".idea"];
const IGNORED_FILES = ["**/.DS_Store", "**/.env", "**/.env.*"];

export async function createZipBuffer(
  directory: string,
  callbacks?: ProgressCallbacks
): Promise<Buffer> {
  const archiver = await import("archiver");
  return new Promise((resolve, reject) => {
    const archive = archiver.default("zip", { zlib: { level: 9 } });
    const chunks: Buffer[] = [];

    archive.on("data", (chunk: Buffer) => chunks.push(chunk));
    archive.on("end", () => resolve(Buffer.concat(chunks)));
    archive.on("error", reject);
    archive.on("progress", (progress) => {
      callbacks?.onProgress?.(progress.entries.processed, progress.entries.total);
    });

    archive.glob("**/*", {
      cwd: directory,
      dot: true,
      skip: IGNORED_DIRS,
      ignore: IGNORED_FILES,
    });
    archive.finalize();
  });
}

function isCI(): boolean {
  return !process.stdout.isTTY || !!process.env.CI;
}

export function createProgressBar(task: string): cliProgress.SingleBar {
  const ci = isCI();
  return new cliProgress.SingleBar({
    format: `${task} [{bar}] {percentage}% | {value}/{total} files`,
    barCompleteChar: "█",
    barIncompleteChar: "░",
    barsize: 20,
    hideCursor: true,
    clearOnComplete: false,
    noTTYOutput: ci,
    notTTYSchedule: ci ? 2000 : 100,
  });
}

export function createSpinner(message: string): { start: () => void; stop: (finalMessage: string) => void } {
  const ci = isCI();
  const frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];
  let frameIndex = 0;
  let interval: NodeJS.Timeout | null = null;

  return {
    start: () => {
      if (ci) {
        console.log(`${message}...`);
        return;
      }
      process.stdout.write(`${frames[0]} ${message}`);
      interval = setInterval(() => {
        frameIndex = (frameIndex + 1) % frames.length;
        process.stdout.write(`\r${frames[frameIndex]} ${message}`);
      }, 80);
    },
    stop: (finalMessage: string) => {
      if (interval) {
        clearInterval(interval);
        process.stdout.write(`\r\x1b[K${finalMessage}\n`);
      } else if (ci) {
        console.log(finalMessage);
      }
    },
  };
}
