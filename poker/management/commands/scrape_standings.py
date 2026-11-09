from django.core.management.base import BaseCommand

from poker import standings


class Command(BaseCommand):
    help = "Scrape the tournament-wide standings from wrgpt.org (skipped if one is recent)."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true")

    def handle(self, *args, **opts):
        snap = standings.scrape(force=opts["force"])
        self.stdout.write("recent snapshot exists; skipped" if snap is None else
                          f"{snap.remaining} remaining, {snap.eliminated} eliminated")
