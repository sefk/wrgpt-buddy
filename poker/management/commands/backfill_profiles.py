from django.core.management.base import BaseCommand

from poker.state import build_profiles


class Command(BaseCommand):
    help = "Rebuild PlayerProfile rows for one table from its actions."

    def add_arguments(self, parser):
        parser.add_argument("table", help="table name (e.g. b17)")

    def handle(self, *args, table, **opts):
        n = build_profiles(table)
        self.stdout.write(f"built/updated {n} profiles for {table}")
