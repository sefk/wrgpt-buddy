from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from poker import gmail, pollstatus, state
from poker.models import RawEmail


MAX_CONSECUTIVE_FAILURES = 3


def poll(service, query: str, log=lambda s: None) -> tuple[int, int]:
    """Fetch and ingest every matching message not already stored. Returns (new, failed).

    Stops early after repeated consecutive failures (e.g. quota exhausted even
    after retries); unfetched messages are picked up by the next poll.
    """
    known = set(RawEmail.objects.exclude(gmail_id=None).values_list("gmail_id", flat=True))
    todo = [gid for gid in gmail.list_message_ids(service, query) if gid not in known]
    new = bad = streak = 0
    for gid in reversed(todo):  # oldest first, so state builds in order
        try:
            _, created, parsed = state.ingest(gmail.fetch_raw(service, gid), "gmail", gmail_id=gid)
        except Exception as exc:
            log(f"{gid}: ERROR {exc}")
            bad += 1
            streak += 1
            if streak >= MAX_CONSECUTIVE_FAILURES:
                log(f"stopping after {streak} consecutive failures; {len(todo) - new - bad} left")
                break
            continue
        streak = 0
        new += created
        err = f" errors={len(parsed.errors)}" if parsed.errors else ""
        log(f"{gid}: {'new' if created else 'matched'} {parsed.meta.kind} {parsed.meta.subject}{err}")
    return new, bad


class Command(BaseCommand):
    help = "Fetch new WRGPT mail from Gmail, store it, and apply it to hand state."

    def add_arguments(self, parser):
        parser.add_argument("--query", default=settings.GMAIL_QUERY)

    def handle(self, *args, **opts):
        try:
            service = gmail.build_service()
        except gmail.GmailAuthError as exc:
            pollstatus.record_failure(str(exc), auth=True)
            raise CommandError(str(exc))
        try:
            new, bad = poll(service, opts["query"], log=self.stdout.write)
        except Exception as exc:
            pollstatus.record_failure(f"{type(exc).__name__}: {exc}")
            raise
        self.stdout.write(f"{new} new, {bad} failed")
        if bad:
            pollstatus.record_failure(f"{bad} messages failed to ingest")
            raise CommandError(f"{bad} messages failed to ingest")
        pollstatus.record_ok()
