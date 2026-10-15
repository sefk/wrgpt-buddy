from django.core.management.base import BaseCommand

from poker import state


class Command(BaseCommand):
    help = "Re-parse every stored RawEmail and rebuild all derived tables."

    def handle(self, *args, **opts):
        n = state.rebuild_all()
        self.stdout.write(f"Rebuilt from {n} emails")
