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
uv run manage.py backfill_analyses --dry-run   # queue analyses of my past moves this year (runs after live work);
                                               # --redo re-analyzes ones already done
uv run pytest poker -q                         # tests
```

## Running locally

```sh
DJANGO_DEBUG=1 uv run manage.py runserver
```

## Deployment (always-on Mac)

Gunicorn listens on `0.0.0.0:8017` (every interface, so also `http://<lan-or-tailnet-ip>:8017`);
static files come from whitenoise
(`staticfiles/`); `tailscale serve` provides tailnet-only HTTPS.

```sh
bin/install-launchd     # renders deploy/launchd/*.plist.in into /Library/LaunchDaemons (sudo), loads all three
bin/deploy              # waits for any running analysis (worker paused), then sync, migrate,
                        # collectstatic, restart web and the recommend worker; --now skips the wait
bin/status              # launchd state (system domain), log tails, last poll result
bin/uninstall-launchd   # sudo
```

The jobs are system LaunchDaemons that run as your user, so they start at boot with nobody
logged in; `bin/install-launchd`, `bin/deploy` and `bin/uninstall-launchd` ask for sudo.
Jobs: `com.sefk.wrgpt-buddy.web` (gunicorn, KeepAlive) and
`com.sefk.wrgpt-buddy.poll` (`bin/poll` every 180s). A failed poll writes
`POLL FAILED (exit N)` and the error to `poll.err.log`, and `bin/status`
reports the non-zero exit. Typical cause: expired Gmail auth, so re-run
`manage.py gmail_auth`.

`com.sefk.wrgpt-buddy.recommend` (`bin/recommend-worker`, KeepAlive) is the
recommendation worker described below. It runs under launchd with `HOME` and a
`PATH` that includes `~/.local/bin` and `/opt/homebrew/bin`, because it shells
out to the `claude` CLI.

Expose it publicly with Tailscale Funnel (one-time; ports 443 and 8443 are
already taken by other Funnel services, so this uses 10000, the third port
Funnel allows). There is no login: anyone with the URL sees everything,
including hole cards for a hand in progress, and can press Ask models.

```sh
tailscale funnel --bg --https=10000 http://127.0.0.1:8017
# tailnet only instead: tailscale funnel --https=10000 off && tailscale serve --bg --https=10000 http://127.0.0.1:8017
```

The app is then at <https://studio.tailc33641.ts.net:10000/>. Host and port are
configurable via `WRGPT_TAILNET_HOST` / `WRGPT_TAILNET_PORT` (used for
`CSRF_TRUSTED_ORIGINS`; `ALLOWED_HOSTS` defaults to `*`).

## Recommendations

For each decision point where it is your turn, the app asks three models what to
do: two local models served by LM Studio (Qwen and gpt-oss) and Sonnet through
the `claude` CLI, all in parallel. A judge turn then continues the Sonnet
conversation, reusing its prompt cache, with both local answers added, and
compares the three. Each local model is graded against Sonnet: agree, same
action with a different size, or different action. Model names and settings are
in `buddy/settings.py` (`ADVICE_*`). Before each local call the app checks
`lms ps` and runs `lms load` (32k context, 1h TTL) if the model isn't loaded,
because JIT loads right after a reboot have stalled. The request timeout then
covers inference only (`ADVICE_LOCAL_TIMEOUT_S`, 300s).

A fourth model, Qwen Flash (`qwen/qwen3.8-flash`), runs on OpenRouter, about
$0.003 per decision, and is counted in the recommendation's cost. Set
`OPENROUTER_API_KEY` in `.env` to enable it; without a key it is skipped. Change
the model with `ADVICE_REMOTE_MODELS` (`model_id=Label`; empty disables it).

The analysis runs automatically but stays hidden. The hand page shows only
"Ready" until you press Show recommendation. You can also press Request
analysis (or Retry / Re-analyze when the hand has moved on) to queue one. The
history page marks revealed results with a dot (all agree) or a diamond
(disagreement).

```sh
uv run manage.py recommend              # analyze once, now
uv run manage.py recommend_worker       # long-running loop that handles queued/automatic requests
uv run manage.py recommend_worker --once  # a single pass, then exit
```

The worker is a launchd daemon (`bin/install-launchd` loads it; `bin/status` shows
its state and log tails). Logs: `~/Library/Logs/wrgpt-buddy/recommend.{out,err}.log`.

## Where things live

- Logs: `~/Library/Logs/wrgpt-buddy/{web,poll,recommend}.{out,err}.log`
- Secrets: `secrets/` (Gmail client and token) and `.env` (Django secret key); both are gitignored
- Database: `db.sqlite3`
- Config reference: `.env.example`

[gcp]: https://console.cloud.google.com/apis/credentials
