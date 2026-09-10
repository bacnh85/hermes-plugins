# b2-backup — Hermes self-backup to Backblaze B2 (restic-encrypted)

Backs up the Hermes home (`~/.hermes`) to Backblaze B2. **Encryption happens
on this machine** — restic encrypts with `RESTIC_PASSWORD` before anything is
uploaded; B2 only ever stores ciphertext. Incremental + deduplicated, with a
retention policy (`forget --prune`).

Each machine backs up to its own repo prefix:

```
b2:<B2_BUCKET>/hermes-selfbackup/<hostname>/     ← this plugin, per-host
b2:<B2_BUCKET>/hermes/                           ← homelab-playbook (SEPARATE — never touched)
```

so several Hermes machines can share one bucket without overwriting each
other, and the playbook's existing `hermes/` repo is never in play.

## Install

```bash
brew install restic                                  # macOS (apt install restic on Debian/Arch)
hermes plugins install bacnh85/hermes-plugins/b2-backup
```

Then add to `~/.hermes/.env` (see `.env.example`):

```
B2_ACCOUNT_ID=<keyID>
B2_APPLICATION_KEY=<applicationKey>
B2_BUCKET=hlab-prod-backup
RESTIC_PASSWORD=<long random passphrase — lose it = lose the backups>
```

Optional settings (config.yaml → `plugins.entries.b2-backup.settings`):

```yaml
plugins:
  entries:
    b2-backup:
      settings:
        repo_prefix: hermes-selfbackup   # bucket path prefix (per-host below it)
        # host: MBP-Sao                  # override repo host segment
        # paths: [/Users/bacnh/.hermes]  # defaults to whole HERMES_HOME
        # excludes: [...]                # merged into the default excludes
        keep_last: 7                     # retention: 7/7/4/6 (playbook parity)
        keep_daily: 7
        keep_weekly: 4
        keep_monthly: 6
        timeout_sec: 3600
```

## Use

```bash
hermes b2backup run                       # backup now
hermes b2backup status                    # repo size + latest snapshot
hermes b2backup snapshots                 # list snapshots
hermes b2backup restore latest --target /tmp/hermes-restore
hermes b2backup restore 9bc6b16a --target /tmp/hermes-restore
hermes b2backup forget                    # apply retention + prune
hermes b2backup check --read-data         # integrity (slow with --read-data)
hermes b2backup verify                    # restore rehearsal: pull config.yaml+.env,
                                          #   hash-compare vs live, clean up (safe)
hermes b2backup restore <id> --dry-run    # preview what would be restored
```

In-session: `/b2backup status`, `/b2backup restore abc12345 target=/tmp/r`.
Agent tool: `hermes_backup_run` (toolset `backup`, actions run/status/
snapshots/restore/verify/forget/unlock/check/init).

## Scheduling

`run` does NOT auto-prune — chain it via Hermes cron (self-contained job,
enable `terminal` toolset):

```
every day at 4:45am: run `hermes b2backup run && hermes b2backup forget`,
then report failures only. Self-contained cron prompt, deliver=telegram.
```

## Restore on a new machine

```bash
brew install restic
export B2_ACCOUNT_ID=... B2_APPLICATION_KEY=... B2_BUCKET=hlab-prod-backup
export RESTIC_PASSWORD=...
restic -r b2:hlab-prod-backup/hermes-selfbackup/<hostname> restore latest --target /tmp/h
```

Safety: `restore` refuses to write into the live Hermes home without
`confirm=true` / `--confirm`; `forget`/`prune` are never part of `run`.

## How it works

- restic **native B2 backend** — no rclone needed, creds flow via
  `B2_ACCOUNT_ID`/`B2_APPLICATION_KEY` env vars per restic docs.
- Secrets come from `~/.hermes/.env` only; `RESTIC_REPOSITORY` is always set
  by the plugin (never inherited from the shell).
- Default excludes skip regenerable state: `hermes-agent` (git), `node`,
  `lsp`, `bin`, `hermes-runtime`, `cache`, `logs`, `pastes`, SQLite sidecars.
  `config.yaml`, `.env`, `state.db`, skills, plugins, cron, sessions ARE backed up.
- One backup at a time per process (module lock). `init` is idempotent.
