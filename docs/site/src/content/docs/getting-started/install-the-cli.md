---
title: Install The CLI
description: Install the Buzz CLI, select a server, and sign in with GitHub.
sidebar:
  order: 2
---

Install the Buzz CLI and connect it to the Buzz server that will host your sites.

## Prerequisites

You need npm, the server URL supplied by your Buzz server operator, and a GitHub account.

## Install Buzz

1. Install the CLI globally:

   ```bash
   npm install --global @infomiho/buzz-cli
   ```

2. Confirm that the `buzz` command is available:

   ```bash
   buzz --version
   ```

## Connect To A Server

1. Save the server URL:

   ```bash
   buzz config server https://buzz.example.com
   ```

2. Start GitHub sign-in:

   ```bash
   buzz login
   ```

3. Open the URL printed by the CLI, enter the displayed code, and approve access. The CLI stores the resulting session for this server in `~/.buzz.config.json`.

4. Verify the session:

   ```bash
   buzz whoami
   ```

Continue with [Deploy Your First Site](../deploy-your-first-site/).
