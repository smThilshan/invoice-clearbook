# Running Clearbook live on this Mac mini

How the live copy is set up and run (decision 77). Everything here runs on the Mac mini, in
the terminal, from the development folder `~/Developer/invoice-clearbook` unless it says
otherwise.

```
Accountants (browser) ── https://<mac>.<tailnet>.ts.net ── Tailscale Funnel
                                                             │
Mac mini ── ~/clearbook-live ─ API + Workbench  127.0.0.1:8100
                             ─ reading worker, posting worker
                             ─ Postgres 5433 (clearbook_live), Redis 6380
            LM Studio (model) 127.0.0.1:1234
            Backup disk ◄── 02:30 every night, encrypted
```

Development stays as it is: Docker Postgres 5432, Redis 6379, `make api` on 8000, `make web` on
5173. The two never share data.

## What is where

| What | Where |
|---|---|
| Live code (never edit it) | `~/clearbook-live` |
| Live settings and secrets | `~/clearbook-live/.env` |
| Invoice files | `~/clearbook-live/data/store` |
| Folders uploaded from accountants' computers | `~/clearbook-live/data/uploads` (removed after 30 days; the invoices stay in the store) |
| Folders runs may use on the server | `~/clearbook-live/inbox` |
| Logs | `~/clearbook-live/logs/` (`api.log`, `worker-reading.log`, `worker-posting.log`, `llm.log`, `backup.log`) |
| Database | Homebrew Postgres 16, port 5433, database `clearbook_live` |
| Backup key | `~/clearbook-live/.restic-password` |
| Services | `~/Library/LaunchAgents/com.missan.clearbook.*.plist` |

## First time

### 1. Before you start
- The code to go live is **committed** on `main`: only committed code is deployed.
- `make check` passes.
- The Mac mini is on a network cable and a UPS (recommended).
- A backup disk is connected, for example `/Volumes/ClearbookBackup`.

### 2. Mac settings
In the terminal:
```
sudo pmset -a sleep 0 disksleep 0 autorestart 1 womp 1
```
That means: never sleep, start again after a power cut, wake for network access.

In **System Settings**:
- **Users & Groups → Automatically log in as**: this user. The services start at login, so after
  a power cut the Mac must log in by itself. This needs FileVault off. If FileVault must stay
  on, someone types the password after every restart.
- **General → Software Update → Automatic updates**: turn off "Install macOS updates". Update
  by hand, outside working hours.
- **LM Studio → Settings**: turn on starting at login, if offered. The live setup also loads the
  model at login.

### 3. Tailscale address
1. Open the **Tailscale** app (already installed), log in with Missan's account.
2. In the Tailscale admin page (login.tailscale.com) → **DNS**: turn on **MagicDNS** and
   **HTTPS Certificates**.
3. Find the address:
   ```
   /Applications/Tailscale.app/Contents/MacOS/Tailscale status --self --json | grep DNSName
   ```
   It looks like `mac-mini.tail1234.ts.net`. The live address is `https://` plus that name,
   without the final dot.

### 4. Install
```
make live-install URL=https://mac-mini.tail1234.ts.net BACKUP=/Volumes/ClearbookBackup/clearbook-backup
```
This does the following:
- installs Postgres 16, Redis and restic;
- makes the live copy and its `.env` with new secrets;
- creates the database;
- installs the services;
- builds and starts everything.

It asks nothing, and it is safe to run again.

**Then save the backup key** in the password manager:
`cat ~/clearbook-live/.restic-password`. Without it the backups cannot be opened.

### 5. Open the door
```
/Applications/Tailscale.app/Contents/MacOS/Tailscale funnel --bg 8100
```
The first time, Tailscale may show a link to allow Funnel for this Mac: open it and allow.
This stays on after restarts.

Test from a phone on **mobile data**, not the office Wi-Fi: open the address. The login page
appears with a lock.

### 6. Users
Live users are made in the live copy, not in development:
```
cd ~/clearbook-live
make add-user EMAIL=you@missan.ae NAME="Your Name" ROLE=admin
make add-user EMAIL=accountant@missan.ae NAME="Name" ROLE=accountant
```

### 7. Accounting systems
- In the **Xero** developer portal, under your app's redirect URIs, add
  `https://<address>/api/xero/callback`.
- For **Zoho**, in its API console, add `https://<address>/api/zoho/callback`.
- The live database is new, so in the Workbench:
  1. create the clients;
  2. **Connect** each one to Xero or Zoho again;
  3. **Sync** the master data.

### 8. Prove the backup
```
make live-backup
make live-restore-test
```
The restore test puts the backup into a spare database and prints the document count of both.
They must match.

## Every day

| Task | Command |
|---|---|
| Is it all running? | `make live-status` |
| Put new committed code live | `make deploy` |
| Go back to the version before | `make deploy REF=<commit>` (the commit is printed by every deploy) |
| Back up now | `make live-backup` |
| See what went wrong | `tail -100 ~/clearbook-live/logs/api.log` (or the worker logs) |
| Restart one service | `launchctl kickstart -k gui/$(id -u)/com.missan.clearbook.api` |

**Deploy outside working hours if you can.** A deploy restarts the services. A file the AI was
reading starts again, and nothing is lost.

**Going back across a database change.** `make deploy` says when the database changed between
two versions. To go back past it, first undo the change, then deploy the earlier commit:
```
cd ~/clearbook-live/backend && uv run alembic downgrade <revision>
```
If unsure, restore last night's backup instead (below).

## When something is wrong

| What you see | Do |
|---|---|
| The link does not open | `make live-status`. Is the Mac on and online? Run `Tailscale funnel status`. |
| Login fails for everyone | Is Redis running? (`make live-status` → health). Then `brew services restart redis` |
| Files stay "waiting for the AI" | Is LM Studio running with the model? `launchctl kickstart -k gui/$(id -u)/com.missan.clearbook.llm`. Files that failed: **Retry** on the run page |
| An upload stops halfway | The connection dropped: choose the folder again and upload again. Files that already arrived are recognised and not doubled |
| Bills do not reach Xero | Open the Review queue: **Waiting to send** / **Could not send** show why. Check `worker-posting.log` |
| Backup did not run | `make live-status` shows the last backup. Is the disk connected? `cat ~/clearbook-live/logs/backup.log` |

## Restoring after a loss
Only when the live database or files are lost or damaged:
1. Stop the services:
   `for s in api worker-reading worker-posting; do launchctl bootout gui/$(id -u)/com.missan.clearbook.$s; done`
2. Restore the backup into a folder:
   `RESTIC_REPOSITORY=$(cat ~/clearbook-live/.backup-repo) RESTIC_PASSWORD_FILE=~/clearbook-live/.restic-password restic restore latest --target /tmp/clearbook-restore`
3. Database:
   `dropdb -h localhost -p 5433 clearbook_live && createdb -h localhost -p 5433 -O clearbook clearbook_live && pg_restore -h localhost -p 5433 --no-owner --role=clearbook -d clearbook_live /tmp/clearbook-restore/backups/clearbook_live.dump`
   The commands are in `/opt/homebrew/opt/postgresql@16/bin`.
4. Files:
   `rsync -a /tmp/clearbook-restore/data/store/ ~/clearbook-live/data/store/`
5. Start again: `make deploy`.
