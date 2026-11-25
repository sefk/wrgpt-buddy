# Log review, 18–25 Nov 2026

Logs in `~/Library/Logs/wrgpt-buddy/` (poll, recommend worker, web), 2026-11-18 00:00 through
2026-11-25 09:35. Per the review's rules, only errors that **repeat across runs** count; one-offs are
listed at the end for reference and are not findings.

Volume: about 475 poll runs a day (every 3 min), 46 recommendations finished, no 5xx responses.

## Findings (repeating errors)

### 1. Poll: `database is locked` during ingest (4 runs, 3 days)

| When | Message |
|------|---------|
| 11-18 07:55 | `1a1ef5c3ba5171fc` (b17 hand 48 broadcast) |
| 11-23 10:21 | `1a209a1f479ff432` (b17 hand 57 reminder) |
| 11-23 12:47 | `1a20a28e7f7a94eb` (b17 hand 57 broadcast) |
| 11-24 12:53 | `1a20f5481dea838e` (b17 hand 61 broadcast) |

Each time, one message hit `ERROR database is locked`, the poll exited 1 (`POLL FAILED`, then
`CommandError: 1 messages failed to ingest`), and the message was ingested by the next poll about
3 minutes later. I checked all four: each is in `poker_rawemail` now. So nothing is lost, but a
game message can reach the app 3 minutes late, which matters when it is my turn to act.

- `buddy/settings.py` sets no SQLite `timeout`, so Django's default of 5 seconds applies.
- Only the 11-23 10:21 failure coincides with a recommend-worker run (hand 57 analysis starting that
  second). The other three have nothing in the recommend log, so the other writer is probably the
  web process or a second poll overlapping. **Cause not verified.**

### 2. Recommend worker: models not loaded, `lms load` (49 times, 8 days)

`openai/gpt-oss-20b not loaded` 30×, `qwen/qwen3.6-35b-a3b not loaded` 19×. This is a WARNING, but it
is the designed behavior: models unload after `ADVICE_LOCAL_TTL_S` (3600 s) and are reloaded before the
next ask, and every load succeeded (no `lms load ... failed` in the window). The cost is about 30 s on
the first ask after an idle hour (e.g. 11-23 10:21, gpt-oss load ≈ 30 s). **Not a bug**; consider
demoting the log level if it is noise.

### 3. Web: `404 /favicon.ico` (16 times, 6 days)

Harmless. There is no favicon. Browsers ask on every first visit.

## One-offs (not counted)

- 11-19 17:16: Qwen Flash on hand 49 took 323.5 s then failed with `JSONDecodeError: Expecting value`
  (OpenRouter returned a non-JSON body; the other opinions completed).
- 11-23 19:02: b17 hand 59 lookahead judge failed with `ValueError: embedded null byte`.
- 11-24 21:45: poll could not resolve `gmail.googleapis.com` (`ServerNotFoundError`). The web server
  restarted in the same minute, so this looks like a reboot or network-down moment.

## Resolution

- Finding 1: SQLite `timeout` raised to 30 s in `buddy/settings.py`. Takes effect on `bin/deploy`.
- Finding 2: the `lms load` message demoted from WARNING to INFO.
- Finding 3: left as is.
