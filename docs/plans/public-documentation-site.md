# Public Documentation Site Plan

Status: Implemented

## Goal

Create a public Buzz manual that is useful to people, search engines, and LLM tools. Host it on GitHub Pages and keep its Markdown source in this repository.

The docs site should become the source of truth for using and operating Buzz. The root README should remain a short introduction with a working quick start and links into the docs.

## Audiences

| Audience | First Goal | Starting Point |
| --- | --- | --- |
| New user | Understand Buzz and deploy one site | Getting started |
| CLI user | Deploy, update, inspect, and automate sites | Guides and CLI reference |
| Server operator | Install and maintain a Buzz server | Self-hosting |
| Contributor | Run the repository and change documented behavior safely | Contributing |

Each page should primarily serve one audience. Pages that mix user, operator, and contributor concerns should be split.

## Proposed Structure

Keep public site sources under `docs/site/`. This prevents internal files under `docs/agents/` from being published accidentally.

```text
docs/
|-- agents/                         # Internal agent instructions, not published
|-- contributing/
|   `-- writing-guide.md            # Documentation policy
|-- plans/
|   `-- public-documentation-site.md
`-- site/
    |-- astro.config.mjs
    |-- package.json
    |-- public/
    |-- scripts/
    |   |-- generate-cli-reference.ts
    |   |-- generate-server-reference.py
    |   `-- check-build.mjs
    `-- src/content/docs/
        |-- index.md
        |-- getting-started/
        |   |-- overview.md
        |   |-- install-the-cli.md
        |   `-- deploy-your-first-site.md
        |-- guides/
        |   |-- deploy-sites.md
        |   |-- choose-a-site-name.md
        |   |-- serve-clean-urls.md
        |   |-- deploy-a-single-page-app.md
        |   |-- use-deployment-tokens.md
        |   |-- deploy-from-ci.md
        |   `-- understand-analytics.md
        |-- self-hosting/
        |   |-- overview.md
        |   |-- docker-compose.md
        |   |-- coolify.md
        |   |-- configure-dns-and-tls.md
        |   |-- configure-github-authentication.md
        |   |-- manage-data-and-backups.md
        |   |-- configure-deployment-limits.md
        |   `-- connect-google-search-console.md
        |   `-- security.md
        |-- reference/
        |   |-- cli/                # Generated from Commander
        |   |-- configuration.md    # Generated from configuration metadata
        |   |-- hosting-behavior.md
        |   `-- http-api/           # Generated from OpenAPI
        |-- troubleshooting/
        |   |-- deployment.md
        |   `-- self-hosting.md
        `-- contributing/
            |-- development.md
            |-- releases.md
            `-- documentation.md
```

This is the target structure, not a requirement to launch every page at once. The first release should cover the quick start, core deployment behavior, Docker Compose, Coolify, configuration, and CLI reference.

## Content Order

Follow the progression used by the Vue documentation guide:

1. Explain what problem Buzz solves.
2. Give the reader a successful deployment in less than ten minutes.
3. Teach the small set of concepts needed for most deployments.
4. Move operator and advanced workflows into focused guides.
5. Keep exhaustive, generated material in reference sections.

Guides are sequential and task-oriented. Reference pages are complete, skimmable, and dictionary-like. Troubleshooting pages start from a symptom and lead to a diagnosis.

## README Migration

The README becomes a landing page, not a second manual.

Keep in `README.md`:

- Project name and one-sentence description.
- A short feature summary.
- A four-step quick start: install, configure the server, sign in, and deploy.
- Links to the documentation, self-hosting guide, changelog, and contributing information.
- A compact local-development section if no contributor guide exists yet.

Move from `README.md`:

| Current Section | Destination |
| --- | --- |
| Usage details | Getting started and deployment guides |
| Requirements | Self-hosting overview |
| DNS records and Cloudflare token | Configure DNS and TLS |
| GitHub OAuth | Configure GitHub authentication |
| Docker deployment | Docker Compose |
| Optional deployment limits | Configuration reference and deployment limits |
| Google search terms | Connect Google Search Console |
| Coolify | Coolify |
| CLI development setup | Contributing development guide |
| How it works | Self-hosting overview |
| Clean URLs, `404.html`, and `200.html` | Hosting behavior and focused guides |
| Releasing | Contributing release guide |

Do not copy the existing text without verification. The current README contains outdated examples, including positional site-name syntax, `buzz config token`, an OAuth callback route that Buzz does not expose, and an incorrect description of where `CNAME` is written. Migrate behavior from code and tests, then replace the README section with a link.

## Sources Of Truth

| Content | Authority | Documentation Method |
| --- | --- | --- |
| CLI commands, arguments, and options | Commander command definitions | Generate Markdown during the docs build |
| HTTP requests and responses | FastAPI models and OpenAPI schema | Generate OpenAPI pages during the docs build |
| Environment variables and defaults | A single server configuration registry | Generate a reference table and `.env.example` |
| Deployment topology | Checked-in Compose files | Explain the intent and link to the source files |
| Hosting behavior | Server code and behavioral tests | Hand-written reference backed by tests |
| Release history | `cli/CHANGELOG.md` | Link or publish without rewriting entries |
| Product terminology and style | Documentation writing guide | Apply during review |

Generated files should include a header that identifies their source and tells contributors not to edit them directly.

## Codebase Preparation

### Make Commander Importable

The CLI currently configures the global Commander program and parses arguments as soon as `cli/src/cli.ts` is imported. Extract a `createProgram()` function and keep argument parsing in the executable entry point. The docs generator can then inspect the same command tree used by the CLI without running commands.

Generate one overview page and one page per command. Start with Commander's rendered help text. Add structured argument and option tables only when they improve scanning enough to justify maintaining the generator.

### Make OpenAPI Publication-Ready

FastAPI already exposes an OpenAPI schema, but it is not yet a complete public contract. Before publishing the HTTP reference:

- Add request and response models to public endpoints.
- Describe authentication with an OpenAPI bearer security scheme.
- Describe the multipart deployment body explicitly.
- Add concise operation summaries, descriptions, and error responses.
- Exclude dashboard implementation routes and other non-public endpoints.
- Generate the schema deterministically with `app.openapi()` during the docs build.

### Consolidate Configuration Metadata

Environment variables are currently repeated across Python constants, `.env.example`, Compose files, the README, and internal instructions. Define each variable once with its name, description, default, required state, sensitivity, and scope. Use that metadata to generate the public configuration reference and validate examples.

### Test Examples And Links

- Run CLI examples against `buzz --help` or focused smoke tests where practical.
- Check internal links during CI.
- Verify that generated CLI and API pages exist before deployment.
- Check that `llms.txt`, `llms-full.txt`, and `llms-small.txt` contain canonical URLs.
- Treat a docs build failure as a pull request failure when public behavior changes.

## Technology Decision

Choose [Astro Starlight](https://starlight.astro.build/).

Starlight is the best fit for Buzz because it is Markdown-first, documentation-focused, visually polished without a large custom theme, statically deployable, and includes local search. Its plugin ecosystem covers the two important generated outputs:

- [`starlight-openapi`](https://starlight-openapi.vercel.app/) generates pages from OpenAPI 3.0 and 3.1 schemas.
- [`starlight-llms-txt`](https://delucis.github.io/starlight-llms-txt/) generates `llms.txt`, `llms-full.txt`, and `llms-small.txt`.

Both are community plugins. Pin their versions, keep generated-output checks in CI, and avoid coupling hand-written guides to plugin-specific components.

### Alternatives Considered

| Option | Strength | Why It Was Not Chosen |
| --- | --- | --- |
| VitePress | Minimal, excellent Markdown authoring, and strong per-page Markdown plugin support | Its OpenAPI path is less compelling for Buzz; it is the close second if exact per-page `.md` URLs become the top priority |
| Docusaurus | Mature OpenAPI, versioning, and internationalization ecosystem | More React, MDX, configuration, and versioning machinery than Buzz currently needs |
| Material for MkDocs | Polished theme and broad historical plugin ecosystem | The Material project and relevant LLM plugin are in maintenance mode during the MkDocs 2 transition, which makes it a poor new dependency |

### GitHub Pages

Deploy from GitHub Actions with a custom workflow because reference generation requires the CLI and server toolchains. For the repository's default project site, configure:

```js
export default defineConfig({
  site: "https://infomiho.github.io",
  base: "/buzz-static-hosting",
});
```

This publishes the site at `https://infomiho.github.io/buzz-static-hosting/`. If Buzz gets a documentation subdomain, set `site` to that origin, remove `base`, and add `public/CNAME`.

A custom documentation domain is preferable for LLM discovery because the proposed conventional location is `/llms.txt`. GitHub project hosting places it at `/buzz-static-hosting/llms.txt`, which the proposal permits but tools may be less likely to discover automatically.

### LLM-Friendly Output

Treat `llms.txt` as a useful proposal, not a guaranteed crawler standard.

Publish:

- `llms.txt` with a short project description and curated links to the quick start, common guides, and reference sections.
- `llms-small.txt` for the essential workflows and terminology.
- `llms-full.txt` for the complete documentation corpus.
- The original Markdown source in the public GitHub repository.

Per-page Markdown endpoints are a second-phase enhancement. Starlight integrations currently use `.md.txt` rather than the proposal's preferred `.md` shape. Add them only after verifying stable URLs and clean output for generated OpenAPI pages.

## Delivery Phases

### Phase 1: Publish The Core Manual

- Scaffold Starlight under `docs/site/`.
- Add the home page, quick start, deployment guides, and self-hosting guides.
- Move and correct the corresponding README content.
- Add Pagefind search, `llms.txt` outputs, link checks, and GitHub Pages deployment.

### Phase 2: Generate Reference Material

- Refactor Commander setup and generate the CLI reference.
- Consolidate environment-variable metadata and generate its reference.
- Improve the public FastAPI schema and add generated HTTP API pages.

### Phase 3: Deepen And Validate

- Add troubleshooting, analytics, backup, security, and contributor pages.
- Evaluate per-page Markdown endpoints.
- Test the docs with common human and LLM questions.
- Remove any remaining duplicated manuals from the repository.

## Launch Criteria

- A new user can deploy a site by following only the quick start.
- An operator can complete Docker Compose or Coolify setup without the README.
- Every released CLI command appears in generated reference content.
- Public environment variables have one documented definition.
- The README is short and links to authoritative docs.
- GitHub Pages serves search, `llms.txt`, and `llms-full.txt` successfully.
- CI detects broken links and stale generated reference content.

## Research Sources

- [Vue documentation writing guide](https://github.com/vuejs/docs/blob/main/.github/contributing/writing-guide.md)
- [Starlight Markdown authoring](https://starlight.astro.build/guides/authoring-content/)
- [Astro GitHub Pages deployment](https://docs.astro.build/en/guides/deploy/github/)
- [Starlight OpenAPI](https://starlight-openapi.vercel.app/getting-started/)
- [Starlight llms.txt](https://delucis.github.io/starlight-llms-txt/getting-started/)
- [llms.txt proposal](https://llmstxt.org/)
