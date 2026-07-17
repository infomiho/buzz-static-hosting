import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  addDomain,
  cancelTransition,
  checkDomain,
  listDomains,
  removeDomain,
  retryTransition,
} from "./domains.js";
import { formatDomainClaim, resolveDomainClaim, type DomainClaim } from "../domains.js";

const cliOptions = { server: "https://buzz.example.com", token: "session-token" };

function capability(status: "disabled" | "unready" | "ready" = "ready") {
  return {
    status,
    detail:
      status === "disabled"
        ? "Custom domains are not enabled on this Buzz server"
        : status === "unready"
          ? "Custom domain control plane is not ready"
          : null,
    enabled: status !== "disabled",
    control_ready: status === "ready",
    admission_enabled: status === "ready",
    routing_enabled: status === "ready",
    routing_targets: [{ type: "A", value: "203.0.113.10" }],
    cloudflare: {
      admission_enabled: false,
      activation_enabled: false,
      ready: false,
      detail: "Cloudflare proxy diagnostics admission is not enabled",
    },
  };
}

function claim(overrides: Partial<DomainClaim> = {}): DomainClaim {
  return {
    id: 7,
    hostname: "www.example.com",
    site_name: "my-site",
    status: "pending",
    verification: {
      type: "TXT",
      name: "_buzz.www.example.com",
      value: "buzz-domain-verification=bdv_test",
    },
    created_at: "2026-07-16T00:00:00+00:00",
    expires_at: "2026-07-17T00:00:00+00:00",
    verified_at: null,
    last_checked_at: null,
    last_error: null,
    route_status: "not_routed",
    route_generation: 0,
    route_error: null,
    challenge_path: null,
    challenge_seen_at: null,
    activated_at: null,
    activation_checked_at: null,
    activation_error: null,
    removal_requested_at: null,
    withdrawn_at: null,
    mode: "direct",
    cloudflare_diagnostics: null,
    ...overrides,
  };
}

function jsonResponse(value: unknown, status = 200, headers?: HeadersInit) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

describe("domain commands", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(console, "log").mockImplementation(() => undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("lists aliases and reports disabled capability without mutation", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(capability("disabled")))
      .mockResolvedValueOnce(jsonResponse([claim()]));

    await listDomains("my-site", cliOptions);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][0]).toBe(
      "https://buzz.example.com/capabilities/custom-domains"
    );
    expect(fetchMock.mock.calls[1][0]).toBe(
      "https://buzz.example.com/sites/my-site/domains"
    );
    expect(fetchMock.mock.calls.every(([, init]) => !init?.method)).toBe(true);
    expect(console.log).toHaveBeenCalledWith(
      "Custom domains are disabled on this Buzz server."
    );
  });

  it("does not add a domain when the capability is unready", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(capability("unready")));

    await expect(addDomain("my-site", "www.example.com", {}, cliOptions)).rejects.toThrow(
      "Custom domain control plane is not ready"
    );

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not fall back when capability information is unavailable", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "Not found" }, 404));

    await expect(listDomains("my-site", cliOptions)).rejects.toMatchObject({
      message: "This Buzz server does not expose custom-domain capability information",
      tip: "Update the server before using CLI domain management",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("rejects malformed capability responses", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ status: "ready" }));

    await expect(listDomains("my-site", cliOptions)).rejects.toThrow(
      "invalid custom-domain capability response"
    );
  });

  it("keeps direct management compatible with pre-Cloudflare servers", async () => {
    const { cloudflare: _cloudflare, ...legacyCapability } = capability();
    const {
      mode: _mode,
      cloudflare_diagnostics: _diagnostics,
      ...legacyClaim
    } = claim();
    fetchMock
      .mockResolvedValueOnce(jsonResponse(legacyCapability))
      .mockResolvedValueOnce(jsonResponse([legacyClaim]));

    await listDomains("my-site", cliOptions);

    expect(console.log).toHaveBeenCalledWith(
      expect.stringContaining("www.example.com")
    );
  });

  it("adds a domain and prints exact DNS instructions", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(capability()))
      .mockResolvedValueOnce(jsonResponse(claim(), 201));

    await addDomain("my-site", "WWW.Example.COM", {}, cliOptions);

    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe("https://buzz.example.com/sites/my-site/domains");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(
      JSON.stringify({ hostname: "WWW.Example.COM", mode: "direct" })
    );
    expect(console.log).toHaveBeenCalledWith("  Name:  _buzz.www.example.com");
    expect(console.log).toHaveBeenCalledWith(
      "  A     www.example.com -> 203.0.113.10"
    );
    expect(console.log).toHaveBeenCalledWith("\nBuzz does not change your DNS records.");
  });

  it("omits mode when the server reports automatic transition readiness", async () => {
    const automaticCapability = {
      ...capability(),
      automatic: { admission_enabled: true, ready: true, detail: null },
    };
    fetchMock
      .mockResolvedValueOnce(jsonResponse(automaticCapability))
      .mockResolvedValueOnce(jsonResponse(claim(), 201));

    await addDomain("my-site", "www.example.com", {}, cliOptions);

    expect(fetchMock.mock.calls[1][1]?.body).toBe(
      JSON.stringify({ hostname: "www.example.com" })
    );
    expect(console.log).toHaveBeenCalledWith(
      "Point this hostname to Buzz using direct DNS or supported Cloudflare proxying:"
    );
  });

  it("does not change the canonical CNAME when adding a domain", async () => {
    const directory = mkdtempSync(join(tmpdir(), "buzz-domain-test-"));
    const cname = join(directory, "CNAME");
    writeFileSync(cname, "my-site\n");
    fetchMock
      .mockResolvedValueOnce(jsonResponse(capability()))
      .mockResolvedValueOnce(jsonResponse(claim(), 201));

    await addDomain("my-site", "www.example.com", {}, cliOptions);

    expect(readFileSync(cname, "utf8")).toBe("my-site\n");
  });

  it("adds an explicit Cloudflare claim while activation is disabled", async () => {
    const cloudflareCapability = {
      ...capability("unready"),
      cloudflare: { admission_enabled: true, ready: true, detail: null },
    };
    const cloudflareClaim = claim({ mode: "cloudflare" });
    fetchMock
      .mockResolvedValueOnce(jsonResponse(cloudflareCapability))
      .mockResolvedValueOnce(jsonResponse(cloudflareClaim, 201));

    await addDomain(
      "my-site",
      "www.example.com",
      { mode: "cloudflare" },
      cliOptions
    );

    expect(fetchMock.mock.calls[1][1]?.body).toBe(
      JSON.stringify({ hostname: "www.example.com", mode: "cloudflare" })
    );
    expect(console.log).toHaveBeenCalledWith(
      "Keep Cloudflare proxying enabled and set SSL/TLS to Full (strict)."
    );
    expect(console.log).toHaveBeenCalledWith(
      "This server currently admits Cloudflare claims for diagnostics only."
    );
    expect(console.log).not.toHaveBeenCalledWith(
      expect.stringContaining("www.example.com ->")
    );
  });

  it("checks the active claim selected by normalized hostname", async () => {
    const checked = claim({
      status: "verified",
      verified_at: "2026-07-16T01:00:00+00:00",
      route_status: "publishing",
    });
    fetchMock
      .mockResolvedValueOnce(jsonResponse(capability()))
      .mockResolvedValueOnce(jsonResponse([claim()]))
      .mockResolvedValueOnce(jsonResponse(checked));

    await checkDomain("my-site", "WWW.Example.COM.", cliOptions);

    expect(fetchMock.mock.calls[2][0]).toBe(
      "https://buzz.example.com/sites/my-site/domains/7/check"
    );
    expect(fetchMock.mock.calls[2][1]?.method).toBe("POST");
  });

  it.each([
    ["retry", retryTransition],
    ["cancel", cancelTransition],
  ] as const)("posts the %s transition action", async (action, command) => {
    const transitioned = claim({
      connection_status: action === "retry" ? "securing" : "connected",
      effective_mode: action === "retry" ? null : "direct",
      target_mode: action === "retry" ? "cloudflare" : null,
    });
    fetchMock
      .mockResolvedValueOnce(jsonResponse(capability()))
      .mockResolvedValueOnce(jsonResponse([claim()]))
      .mockResolvedValueOnce(jsonResponse(transitioned));

    await command("my-site", "www.example.com", cliOptions);

    expect(fetchMock.mock.calls[2][0]).toBe(
      `https://buzz.example.com/sites/my-site/domains/7/transition/${action}`
    );
    expect(fetchMock.mock.calls[2][1]?.method).toBe("POST");
  });

  it("checks ownership while new-domain admission is closed", async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse({
          ...capability("unready"),
          detail: "New custom domain claims are not enabled on this Buzz server",
          control_ready: true,
        })
      )
      .mockResolvedValueOnce(jsonResponse([claim()]))
      .mockResolvedValueOnce(jsonResponse(claim({ last_error: "txt_mismatch" })));

    await checkDomain("my-site", "www.example.com", cliOptions);

    expect(fetchMock.mock.calls[2][1]?.method).toBe("POST");
  });

  it("reports the ownership-check retry interval", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(capability()))
      .mockResolvedValueOnce(jsonResponse([claim()]))
      .mockResolvedValueOnce(
        jsonResponse(
          { detail: "Wait before checking this custom domain again" },
          429,
          { "Retry-After": "42" }
        )
      );

    await expect(
      checkDomain("my-site", "www.example.com", cliOptions)
    ).rejects.toMatchObject({
      message: "Wait before checking this custom domain again",
      tip: "Retry in 42 seconds",
    });
  });

  it("does not delete when removal is not confirmed", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(capability()))
      .mockResolvedValueOnce(jsonResponse([claim()]));
    const confirm = vi.fn().mockResolvedValue(false);

    await removeDomain("my-site", "www.example.com", {}, cliOptions, { confirm });

    expect(confirm).toHaveBeenCalledOnce();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(console.log).toHaveBeenCalledWith("Aborted.");
  });

  it("reports asynchronous router withdrawal", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(capability("unready")))
      .mockResolvedValueOnce(
        jsonResponse([claim({ status: "verified", route_status: "routed" })])
      )
      .mockResolvedValueOnce(new Response(null, { status: 202 }));
    const confirm = vi.fn();

    await removeDomain(
      "my-site",
      "www.example.com",
      { yes: true },
      cliOptions,
      { confirm }
    );

    expect(confirm).not.toHaveBeenCalled();
    expect(fetchMock.mock.calls[2][1]?.method).toBe("DELETE");
    expect(console.log).toHaveBeenCalledWith("Removal requested for www.example.com.");
  });

  it("allows user removal during operator withdrawal", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(capability("unready")))
      .mockResolvedValueOnce(
        jsonResponse([
          claim({
            status: "verified",
            route_status: "removing",
            removal_requested_at: null,
          }),
        ])
      )
      .mockResolvedValueOnce(new Response(null, { status: 202 }));

    await removeDomain("my-site", "www.example.com", { yes: true }, cliOptions);

    expect(fetchMock.mock.calls[2][1]?.method).toBe("DELETE");
  });

  it("does not mutate an inactive claim", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(capability()))
      .mockResolvedValueOnce(
        jsonResponse([claim({ status: "cancelled", route_status: "removed" })])
      );

    await removeDomain("my-site", "www.example.com", { yes: true }, cliOptions);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(console.log).toHaveBeenCalledWith(
      "Custom domain www.example.com is already inactive."
    );
  });

  it("explains deployment-token rejection", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: "Deploy tokens cannot perform this operation" }, 403)
    );

    await expect(listDomains("my-site", cliOptions)).rejects.toMatchObject({
      message: "Deployment tokens cannot manage custom domains",
      tip: "Run 'buzz login' and retry with a full session",
    });
  });
});

describe("domain presentation", () => {
  it("prefers the newest active claim", () => {
    const active = claim({ id: 3 });
    const historical = claim({ id: 9, status: "expired" });

    expect(resolveDomainClaim([historical, active], "WWW.Example.COM.").id).toBe(3);
  });

  it("normalizes equivalent unicode hostname separators", () => {
    expect(resolveDomainClaim([claim()], "www.example.com。").id).toBe(7);
  });

  it("formats activation and removal states", () => {
    const output = formatDomainClaim(
      claim({
        status: "verified",
        route_status: "removing",
        route_error: "withdrawal_snapshot_not_acknowledged",
        activated_at: "2026-07-16T02:00:00+00:00",
        removal_requested_at: "2026-07-16T03:00:00+00:00",
      })
    );

    expect(output).toContain("Ownership:   Verified");
    expect(output).toContain("Routing DNS: Not checked");
    expect(output).toContain("Waiting for Traefik to poll withdrawal");
    expect(output).toContain("Public TLS:  Not active");
    expect(output).toContain("Removal:     In progress");
  });

  it("formats automatic connection paths separately", () => {
    const output = formatDomainClaim(
      claim({
        connection_status: "updating",
        effective_mode: "direct",
        observed_mode: "cloudflare",
        target_mode: "cloudflare",
      })
    );

    expect(output).toContain("Connection:  Updating connection");
    expect(output).toContain("Effective:   Direct");
    expect(output).toContain("Observed:    Cloudflare proxy");
    expect(output).toContain("Target:      Cloudflare proxy");
    expect(output).toContain("Public TLS:  Authorization retained");
    expect(output).not.toContain("Public TLS:  Active");
  });

  it("keeps unknown diagnostic codes visible", () => {
    expect(formatDomainClaim(claim({ route_error: "future_error" }))).toContain(
      "Router:      Error: future_error"
    );
  });

  it("reports activation only for a currently routed claim", () => {
    const output = formatDomainClaim(
      claim({
        status: "verified",
        route_status: "routed",
        activated_at: "2026-07-16T02:00:00+00:00",
      })
    );

    expect(output).toContain("Routing DNS: Valid");
    expect(output).toContain("Origin TLS:  Trusted");
    expect(output).toContain("Public TLS:  Active");
  });

  it("distinguishes operator withdrawal from requested removal", () => {
    expect(
      formatDomainClaim(
        claim({ status: "verified", route_status: "removing" })
      )
    ).toContain("Removal:     Not requested (router withdrawal in progress)");
  });

  it("formats Cloudflare edge and origin diagnostics separately", () => {
    const output = formatDomainClaim(
      claim({
        mode: "cloudflare",
        status: "verified",
        route_status: "routed",
        cloudflare_diagnostics: {
          generation: 1,
          checked_at: "2026-07-16T00:00:00+00:00",
          ranges_version: "2026-07-16",
          ownership: { status: "healthy", error: null },
          dns: { status: "healthy", error: null },
          edge_tls: { status: "healthy", error: null },
          edge_http: {
            status: "failed",
            error: "cloudflare_526",
            status_code: 526,
            address: "104.16.0.1",
            cf_ray: "ray",
            cf_cache_status: null,
            redirect_location: null,
          },
          http_forwarding: {
            status: "failed",
            error: "http_forward_redirect",
            status_code: 301,
          },
          origin: { status: "failed", error: "origin_tls_invalid" },
          consecutive_failures: 1,
        },
      })
    );

    expect(output).toContain("Mode:            Cloudflare proxy");
    expect(output).toContain("Ownership:       healthy");
    expect(output).toContain("Cloudflare DNS:  healthy");
    expect(output).toContain("Edge challenge:  cloudflare_526");
    expect(output).toContain("HTTP forwarding: http_forward_redirect");
    expect(output).toContain("Origin:          origin_tls_invalid");
    expect(output).toContain("Activation:      Not active");
  });

  it("shows pending Cloudflare ownership before diagnostics begin", () => {
    const output = formatDomainClaim(
      claim({ mode: "cloudflare", status: "pending", cloudflare_diagnostics: null })
    );

    expect(output).toContain("Ownership:       Pending");
    expect(output).not.toContain("Ownership:       Waiting for router acknowledgement");
    expect(output).not.toContain("Cloudflare DNS:");
    expect(output).not.toContain("Edge TLS:");
  });

  it("uses authoritative connection state for Cloudflare activation", () => {
    const output = formatDomainClaim(
      claim({
        mode: "cloudflare",
        status: "verified",
        route_status: "routed",
        activated_at: "2026-07-16T02:00:00+00:00",
        connection_status: "action_needed",
        effective_mode: null,
      })
    );

    expect(output).toContain("Activation:      Not active");
    expect(output).not.toContain("Activation:      Active");
  });

  it.each([
    ["pending", "Pending"],
    ["verified", "Verified"],
    ["expired", "Expired"],
    ["cancelled", "Cancelled"],
  ] as const)("formats the %s ownership state", (status, expected) => {
    expect(formatDomainClaim(claim({ status }))).toContain(`Ownership:   ${expected}`);
  });

  it.each([
    ["not_routed", "Not published"],
    ["publishing", "Publishing, waiting for Traefik acknowledgement"],
    ["routed", "Acknowledged"],
    ["removing", "Removing, waiting for withdrawal acknowledgement"],
    ["removed", "Withdrawn"],
  ] as const)("formats the %s router state", (routeStatus, expected) => {
    expect(formatDomainClaim(claim({ route_status: routeStatus }))).toContain(
      `Router:      ${expected}`
    );
  });

  it.each([
    ["dns_no_addresses", "No A or AAAA addresses found"],
    ["dns_unexpected_address", "Does not resolve only to this Buzz server"],
    ["dns_unavailable", "Temporarily unavailable; retrying"],
    ["tls_invalid", "Waiting for a trusted certificate"],
    ["origin_unavailable", "Origin unavailable; retrying"],
    ["challenge_mismatch", "Origin challenge mismatch; retrying"],
    ["activation_check_failed", "Validation failed unexpectedly; retrying"],
  ] as const)("formats the %s activation diagnostic", (error, expected) => {
    expect(
      formatDomainClaim(claim({ route_status: "routed", activation_error: error }))
    ).toContain(expected);
  });
});
