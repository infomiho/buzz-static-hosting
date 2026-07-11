---
title: Manage Data And Backups
description: Back up and restore the persistent Buzz volume while the server is stopped.
sidebar:
  order: 6
---

Protect deployed sites and Buzz metadata by backing up the complete data volume. The documented procedure is a cold backup: stop Buzz before copying `/data` so site files and the SQLite database represent one point in time.

## Know What Must Persist

Both bundled deployments mount the named Docker volume `buzz_buzz-data` at `/data`. It contains:

- `/data/data.db`, including sites, owners, sessions, deployment tokens, and analytics.
- `/data/sites`, including every deployed site's files and deployment operation journals.

Back up both paths together. Copying only site files loses ownership and token metadata. Copying only `data.db` loses deployed content.

Treat backups as sensitive. The database contains user identity data and token hashes, and deployed files can contain information that wasn't intended for another operator.

The standalone deployment also uses `buzz_traefik-certs` for certificate state. It isn't Buzz application data, but backing it up can avoid unnecessary certificate reissuance after host recovery.

## Create A Cold Backup

These commands run from the directory containing the active Compose file. They cause downtime until the application starts again.

1. Stop the application without deleting its volumes:

   ```bash
   docker compose down
   ```

2. Archive the data volume into the current directory:

   ```bash
   docker run --rm -v buzz_buzz-data:/data:ro -v "$PWD":/backup alpine tar czf /backup/buzz-data.tar.gz -C /data .
   ```

3. Inspect the archive before restarting:

   ```bash
   tar -tzf buzz-data.tar.gz
   ```

   Confirm that the listing contains `./data.db` and `./sites/`.

4. Move the archive to storage outside the Docker host, then restart Buzz:

   ```bash
   docker compose up -d
   curl --fail --show-error https://buzz.example.com/health
   ```

Buzz doesn't currently document or test a hot-backup method. A filesystem copy while Buzz is accepting deployments or analytics writes may not produce a consistent restore point.

## Restore A Backup

Restore into a new volume so the current Buzz data remains available for rollback.

1. Stop the application:

   ```bash
   docker compose down
   ```

2. Rename the current volume instead of overwriting it in place:

   ```bash
   docker volume create buzz_buzz-data-restored
   docker run --rm -v buzz_buzz-data-restored:/data -v "$PWD":/backup:ro alpine tar xzf /backup/buzz-data.tar.gz -C /data
   ```

3. Create `compose.restore.yml` next to the active Compose file:

   ```yaml
   volumes:
     buzz-data:
       name: buzz_buzz-data-restored
   ```

4. Start Buzz with the restored volume:

   ```bash
   docker compose -f docker-compose.yml -f compose.restore.yml up -d
   ```

5. Verify the health endpoint, dashboard site list, and one known site URL.

If verification fails, stop Buzz and start the original Compose file without `compose.restore.yml`.

The bundled Coolify deployment uses the same volume name. Run backup commands on the Coolify host during a maintenance window, and make persistent Compose changes through the Coolify-managed application configuration.
