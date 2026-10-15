# wrgpt-buddy

Local companion for the WRGPT email poker game. It ingests the dealer's emails
(from Gmail or `.eml` files), parses them into hand state, and serves a small
Django web UI. See `spec.md` for the design.

## Setup

```sh
uv sync
uv run manage.py migrate
cp .env.example .env          # then set DJANGO_SECRET_KEY (bin/deploy does this for you)
```

Gmail OAuth: create a Desktop OAuth client in Google Cloud ([console][gcp]),
save it as `secrets/gmail-client.json`, then:

```sh
uv run manage.py gmail_auth   # opens a browser, writes secrets/gmail-token.json
```

## Everyday commands

```sh
uv run manage.py import_eml path/to/file.eml   # ingest a saved email
uv run manage.py poll_gmail                    # fetch new mail from Gmail
uv run manage.py reparse                       # re-run parsers over stored mail
uv run pytest poker -q                         # tests
```

## Running locally

```sh
DJANGO_DEBUG=1 uv run manage.py runserver
```

## Deployment (always-on Mac)

Gunicorn listens on `127.0.0.1:8017`; static files come from whitenoise
(`staticfiles/`); `tailscale serve` provides tailnet-only HTTPS.

```sh
bin/install-launchd     # renders deploy/launchd/*.plist.in, loads both agents
bin/deploy              # sync, migrate, collectstatic, restart web
bin/status              # launchd state, log tails, last poll result
bin/uninstall-launchd
```

Agents: `com.sefk.wrgpt-buddy.web` (gunicorn, KeepAlive) and
`com.sefk.wrgpt-buddy.poll` (`bin/poll` every 180s). A failed poll writes
`POLL FAILED (exit N)` and the error to `poll.err.log`, and `bin/status`
reports the non-zero exit. Typical cause: expired Gmail auth, so re-run
`manage.py gmail_auth`.

Expose on the tailnet (one-time; port 443 and 8443 are already taken by
Funnel services, so this uses 10000, which is not public):

```sh
tailscale serve --bg --https=10000 http://127.0.0.1:8017
```

The app is then at <https://studio.tailc33641.ts.net:10000/>. Host and port are
configurable via `WRGPT_TAILNET_HOST` / `WRGPT_TAILNET_PORT` (used for
`ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`).

## Where things live

- Logs: `~/Library/Logs/wrgpt-buddy/{web,poll}.{out,err}.log`
- Secrets: `secrets/` (Gmail client and token) and `.env` (Django secret key); both are gitignored
- Database: `db.sqlite3`
- Config reference: `.env.example`

[gcp]: https://console.cloud.google.com/apis/credentials
