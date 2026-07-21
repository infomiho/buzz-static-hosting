import { afterEach, describe, expect, it, vi } from "vitest";
import {
  apiFetch,
  authHeaders,
  isRecord,
  requestEmpty,
  requestJson,
  type Guard,
} from "./client.js";
import { CliError } from "./errors.js";

const cliOptions = { server: "https://buzz.example.com", token: "session-token" };

const anyGuard: Guard<unknown> = (_value: unknown): _value is unknown => true;

function fetchOnce(response: Response): typeof fetch {
  return async () => response;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
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

describe("apiFetch authentication", () => {
  it("throws before fetching when a required token is missing", async () => {
    vi.stubEnv("BUZZ_TOKEN", "");
    const fetchFn = vi.fn<typeof fetch>();

    await expect(
      apiFetch("/x", {}, { cliOptions: { server: "http://no-token.invalid" }, fetchFn })
    ).rejects.toMatchObject({
      message: "Not authenticated",
      tip: "Run 'buzz login' first",
    });
    expect(fetchFn).not.toHaveBeenCalled();
  });

  it("sends the bearer header when authenticated", async () => {
    const fetchFn = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response(null, { status: 204 }));

    await requestEmpty("/x", [204], {}, { cliOptions, fetchFn });

    const headers = fetchFn.mock.calls[0][1]?.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer session-token");
  });

  it("omits the bearer header when auth is none", async () => {
    const fetchFn = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response(null, { status: 204 }));

    await requestEmpty("/x", [204], {}, { auth: "none", cliOptions, fetchFn });

    const headers = fetchFn.mock.calls[0][1]?.headers as Record<string, string>;
    expect(headers?.Authorization).toBeUndefined();
  });

  it("does not remap 401 responses when auth is none", async () => {
    const fetchFn = fetchOnce(new Response(null, { status: 401 }));

    const response = await apiFetch("/x", {}, { auth: "none", cliOptions, fetchFn });

    expect(response.status).toBe(401);
  });
});

describe("apiFetch errors", () => {
  it("wraps fetch failures as connection errors", async () => {
    const fetchFn: typeof fetch = async () => {
      throw new Error("ECONNREFUSED");
    };

    await expect(apiFetch("/x", {}, { cliOptions, fetchFn })).rejects.toThrow(
      "Could not connect to server - ECONNREFUSED"
    );
  });

  it("maps 401 to a session-expired error by default", async () => {
    const fetchFn = fetchOnce(new Response(null, { status: 401 }));

    await expect(apiFetch("/x", {}, { cliOptions, fetchFn })).rejects.toMatchObject({
      message: "Session expired",
      tip: "Run 'buzz login' to re-authenticate",
    });
  });

  it("allows overriding the 401 error", async () => {
    const fetchFn = fetchOnce(new Response(null, { status: 401 }));

    await expect(
      apiFetch(
        "/x",
        {},
        { cliOptions, fetchFn, errors: { unauthorized: new CliError("Not authenticated") } }
      )
    ).rejects.toThrow("Not authenticated");
  });

  it("maps 403 to the server detail by default", async () => {
    const fetchFn = fetchOnce(
      new Response(JSON.stringify({ detail: "Nope" }), { status: 403 })
    );

    await expect(apiFetch("/x", {}, { cliOptions, fetchFn })).rejects.toThrow("Nope");
  });

  it("uses permission denied for an empty 403 body", async () => {
    const fetchFn = fetchOnce(new Response(null, { status: 403 }));

    await expect(apiFetch("/x", {}, { cliOptions, fetchFn })).rejects.toThrow(
      "Permission denied"
    );
  });

  it("routes 403 through the forbidden mapper", async () => {
    const fetchFn = fetchOnce(
      new Response(JSON.stringify({ detail: "special" }), { status: 403 })
    );

    await expect(
      apiFetch(
        "/x",
        {},
        { cliOptions, fetchFn, errors: { forbidden: (m) => new CliError(`mapped:${m}`) } }
      )
    ).rejects.toThrow("mapped:special");
  });
});

describe("requestJson", () => {
  it("returns a guarded body", async () => {
    const fetchFn = fetchOnce(
      new Response(JSON.stringify({ ok: true }), { status: 200 })
    );
    const guard: Guard<{ ok: true }> = (v): v is { ok: true } =>
      isRecord(v) && v.ok === true;

    await expect(
      requestJson("/x", { guard, invalid: "bad shape" }, {}, { cliOptions, fetchFn })
    ).resolves.toEqual({ ok: true });
  });

  it("applies normalize before the guard", async () => {
    const fetchFn = fetchOnce(new Response(JSON.stringify({}), { status: 200 }));
    const guard: Guard<{ mode: string }> = (v): v is { mode: string } =>
      isRecord(v) && typeof v.mode === "string";

    await expect(
      requestJson(
        "/x",
        {
          guard,
          invalid: "bad shape",
          normalize: (v) => ({ ...(v as object), mode: "direct" }),
        },
        {},
        { cliOptions, fetchFn }
      )
    ).resolves.toEqual({ mode: "direct" });
  });

  it("rejects a response that fails the guard", async () => {
    const fetchFn = fetchOnce(
      new Response(JSON.stringify({ wrong: true }), { status: 200 })
    );
    const guard: Guard<{ ok: true }> = (v): v is { ok: true } =>
      isRecord(v) && v.ok === true;

    await expect(
      requestJson("/x", { guard, invalid: "bad shape" }, {}, { cliOptions, fetchFn })
    ).rejects.toThrow("bad shape");
  });

  it("maps 404 to a notFound string", async () => {
    const fetchFn = fetchOnce(new Response(null, { status: 404 }));

    await expect(
      requestJson(
        "/x",
        { guard: anyGuard, invalid: "invalid" },
        {},
        { cliOptions, fetchFn, errors: { notFound: "missing" } }
      )
    ).rejects.toThrow("missing");
  });

  it("maps 404 to a notFound CliError", async () => {
    const fetchFn = fetchOnce(new Response(null, { status: 404 }));

    await expect(
      requestJson(
        "/x",
        { guard: anyGuard, invalid: "invalid" },
        {},
        { cliOptions, fetchFn, errors: { notFound: new CliError("gone", "look elsewhere") } }
      )
    ).rejects.toMatchObject({ message: "gone", tip: "look elsewhere" });
  });

  it("reads FastAPI detail errors on failure", async () => {
    const fetchFn = fetchOnce(
      new Response(JSON.stringify({ detail: "Nope" }), { status: 500 })
    );

    await expect(
      requestJson("/x", { guard: anyGuard, invalid: "invalid" }, {}, { cliOptions, fetchFn })
    ).rejects.toThrow("Nope");
  });

  it("falls back to text errors on failure", async () => {
    const fetchFn = fetchOnce(new Response("plain failure", { status: 500 }));

    await expect(
      requestJson("/x", { guard: anyGuard, invalid: "invalid" }, {}, { cliOptions, fetchFn })
    ).rejects.toThrow("plain failure");
  });

  it("uses the fallback for an empty error body", async () => {
    const fetchFn = fetchOnce(new Response(null, { status: 500 }));

    await expect(
      requestJson(
        "/x",
        { guard: anyGuard, invalid: "invalid" },
        {},
        { cliOptions, fetchFn, errors: { fallback: "boom" } }
      )
    ).rejects.toThrow("boom");
  });

  it("adds a Retry-After tip to failures", async () => {
    const fetchFn = fetchOnce(
      new Response(JSON.stringify({ detail: "later" }), {
        status: 429,
        headers: { "Retry-After": "30" },
      })
    );

    await expect(
      requestJson("/x", { guard: anyGuard, invalid: "invalid" }, {}, { cliOptions, fetchFn })
    ).rejects.toMatchObject({ message: "later", tip: "Retry in 30 seconds" });
  });
});

describe("requestEmpty", () => {
  it("returns the matched status", async () => {
    const fetchFn = fetchOnce(new Response(null, { status: 202 }));

    await expect(
      requestEmpty("/x", [202, 204], { method: "DELETE" }, { cliOptions, fetchFn })
    ).resolves.toBe(202);
  });

  it("maps 404 to notFound", async () => {
    const fetchFn = fetchOnce(new Response(null, { status: 404 }));

    await expect(
      requestEmpty("/x", [204], {}, { cliOptions, fetchFn, errors: { notFound: "gone" } })
    ).rejects.toThrow("gone");
  });

  it("throws the fallback for an unmatched status", async () => {
    const fetchFn = fetchOnce(
      new Response(JSON.stringify({ detail: "nope" }), { status: 500 })
    );

    await expect(
      requestEmpty("/x", [204], {}, { cliOptions, fetchFn })
    ).rejects.toThrow("nope");
  });
});
