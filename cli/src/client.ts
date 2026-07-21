import { CliError } from "./errors.js";
import {
  DEFAULT_SERVER,
  getCredential,
  loadConfig,
  normalizeServerUrl,
} from "./credentials.js";

export { CliError } from "./errors.js";

export type Guard<T> = (value: unknown) => value is T;

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export interface CliOptions {
  server?: string;
  token?: string;
}

export interface ResolvedOptions {
  server: string;
  token?: string;
}

export function getOptions(cliOptions: CliOptions = {}): ResolvedOptions {
  const config = loadConfig();
  const server = normalizeServerUrl(
    cliOptions.server || process.env.BUZZ_SERVER || config.server || DEFAULT_SERVER
  );
  return {
    server,
    token:
      cliOptions.token || process.env.BUZZ_TOKEN || getCredential(config, server),
  };
}

export function authHeaders(token?: string): Record<string, string> {
  if (token) {
    return { Authorization: `Bearer ${token}` };
  }
  return {};
}

export interface Site {
  name: string;
  created: string;
  size_bytes: number;
}

export function isSiteArray(value: unknown): value is Site[] {
  return (
    Array.isArray(value) &&
    value.every((site) => isRecord(site) && typeof site.name === "string")
  );
}

export interface ApiErrors {
  unauthorized?: CliError;
  forbidden?: (serverMessage: string) => CliError;
  notFound?: string | CliError;
  fallback?: string;
}

export interface ApiOptions {
  auth?: "required" | "none";
  cliOptions?: CliOptions;
  fetchFn?: typeof fetch;
  errors?: ApiErrors;
}

export interface JsonSpec<T> {
  guard: Guard<T>;
  invalid: string;
  normalize?: (value: unknown) => unknown;
}

async function errorMessage(response: Response, fallback: string): Promise<string> {
  const text = await response.text();
  if (!text) return fallback;

  try {
    const data: unknown = JSON.parse(text);
    if (data && typeof data === "object") {
      const { detail, error } = data as { detail?: unknown; error?: unknown };
      if (typeof detail === "string") return detail;
      if (typeof error === "string") return error;
    }
  } catch {
    return text;
  }

  return fallback;
}

async function failure(response: Response, fallback: string): Promise<CliError> {
  const message = await errorMessage(response, fallback);
  const retryAfter = response.headers.get("Retry-After");
  return new CliError(message, retryAfter ? `Retry in ${retryAfter} seconds` : undefined);
}

function notFoundError(errors: ApiErrors): CliError | undefined {
  if (errors.notFound === undefined) return undefined;
  return typeof errors.notFound === "string"
    ? new CliError(errors.notFound)
    : errors.notFound;
}

async function httpError(response: Response, errors: ApiErrors): Promise<CliError> {
  if (response.status === 404) {
    const mapped = notFoundError(errors);
    if (mapped) return mapped;
  }
  return failure(response, errors.fallback ?? "Unknown error");
}

export async function apiFetch(
  path: string,
  init: RequestInit = {},
  opts: ApiOptions = {}
): Promise<Response> {
  const {
    auth = "required",
    cliOptions = {},
    fetchFn = globalThis.fetch,
    errors = {},
  } = opts;
  const options = getOptions(cliOptions);

  if (auth === "required" && !options.token) {
    throw new CliError("Not authenticated", "Run 'buzz login' first");
  }

  let response: Response;
  try {
    response = await fetchFn(`${options.server}${path}`, {
      ...init,
      headers: {
        ...(auth === "none" ? {} : authHeaders(options.token)),
        ...init.headers,
      },
    });
  } catch (error) {
    throw new CliError(
      `Could not connect to server - ${error instanceof Error ? error.message : error}`
    );
  }

  if (auth !== "none") {
    if (response.status === 401) {
      throw (
        errors.unauthorized ??
        new CliError("Session expired", "Run 'buzz login' to re-authenticate")
      );
    }
    if (response.status === 403) {
      const message = await errorMessage(response, "Permission denied");
      throw errors.forbidden ? errors.forbidden(message) : new CliError(message);
    }
  }

  return response;
}

export async function requestJson<T>(
  path: string,
  spec: JsonSpec<T>,
  init: RequestInit = {},
  opts: ApiOptions = {}
): Promise<T> {
  const response = await apiFetch(path, init, opts);
  if (!response.ok) {
    throw await httpError(response, opts.errors ?? {});
  }

  const raw: unknown = await response.json();
  const value = spec.normalize ? spec.normalize(raw) : raw;
  if (!spec.guard(value)) {
    throw new CliError(spec.invalid);
  }
  return value;
}

export async function requestEmpty<S extends number>(
  path: string,
  okStatuses: readonly S[],
  init: RequestInit = {},
  opts: ApiOptions = {}
): Promise<S> {
  const response = await apiFetch(path, init, opts);
  const matched = okStatuses.find((status) => status === response.status);
  if (matched !== undefined) {
    return matched;
  }
  throw await httpError(response, opts.errors ?? {});
}
