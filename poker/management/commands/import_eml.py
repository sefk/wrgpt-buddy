from pathlib import Path

from django.core.management.base import BaseCommand

from poker import state
from poker.models import RawEmail


class Command(BaseCommand):
    help = "Import .eml files (or directories of them): store, parse, apply."

    def add_arguments(self, parser):
        parser.add_argument("paths", nargs="+")

    def handle(self, *args, **opts):
        files = []
        for p in map(Path, opts["paths"]):
            files += sorted(p.rglob("*.eml")) if p.is_dir() else [p]
        new = dup = bad = 0
        for f in files:
            data = f.read_bytes()
            try:
                parsed = state.parse_raw_bytes(data)
            except Exception as exc:  # parse_email shouldn't raise; be safe
                self.stderr.write(f"{f.name}: ERROR {exc}")
                bad += 1
                continue
            mid = parsed.meta.message_id or f"file:{f.name}"
            raw, created = RawEmail.objects.get_or_create(
                message_id=mid, defaults={"raw": data, "source": "eml"}
            )
            state.refresh_meta(raw, parsed)
            state.apply(raw, parsed)
            if created:
                new += 1
            else:
                dup += 1
            err = f" errors={len(parsed.errors)}" if parsed.errors else ""
            self.stdout.write(
                f"{f.name}: {'new' if created else 'dup'} {parsed.meta.kind}{err}"
            )
        self.stdout.write(f"{new} new, {dup} duplicate, {bad} failed")
