from pathlib import Path

from django.core.management.base import BaseCommand

from poker import state


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
            try:
                _, created, parsed = state.ingest(f.read_bytes(), "eml")
            except Exception as exc:  # parse_email shouldn't raise; be safe
                self.stderr.write(f"{f.name}: ERROR {exc}")
                bad += 1
                continue
            if created:
                new += 1
            else:
                dup += 1
            err = f" errors={len(parsed.errors)}" if parsed.errors else ""
            self.stdout.write(
                f"{f.name}: {'new' if created else 'dup'} {parsed.meta.kind}{err}"
            )
        self.stdout.write(f"{new} new, {dup} duplicate, {bad} failed")
