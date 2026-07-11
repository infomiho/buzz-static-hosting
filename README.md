# Buzz

Buzz is a self-hosted static site server with a CLI for deploying directories.

## Features

- Deploy or replace a site with one command.
- Serve clean URLs, custom `404.html` pages, and single-page apps with a `200.html` fallback.
- Manage sites through the CLI and browser dashboard.
- Sign in with GitHub or use site-scoped deployment tokens for automation.
- Record site traffic and optionally include Google Search Console queries.

## Quick Start

You need Node.js 22 or later, npm, a GitHub account, access to a running Buzz server, and a directory of built static files.

1. Install the CLI:

   ```bash
   npm install --global @infomiho/buzz-cli
   ```

2. Configure your server URL:

   ```bash
   buzz config server https://buzz.example.com
   ```

3. Sign in with GitHub. Follow the printed URL and enter the displayed code:

   ```bash
   buzz login
   ```

4. Deploy your site:

   ```bash
   buzz deploy ./dist --subdomain my-site
   ```

Buzz prints the site URL and writes `my-site` to a `CNAME` file in your current working directory. A later deployment from that directory reuses the site name unless you pass `--subdomain` again.

## Documentation

- [Buzz documentation](https://infomiho.github.io/buzz-static-hosting/)
- [Self-host Buzz](https://infomiho.github.io/buzz-static-hosting/self-hosting/overview/)
- [CLI reference](https://infomiho.github.io/buzz-static-hosting/reference/cli/)
- [Changelog](cli/CHANGELOG.md)

## Contributing

- [Set up a development environment](https://infomiho.github.io/buzz-static-hosting/contributing/development/)
- [Write and test documentation](https://infomiho.github.io/buzz-static-hosting/contributing/documentation/)
- [Understand the release process](https://infomiho.github.io/buzz-static-hosting/contributing/releases/)
