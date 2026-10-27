from django.core.management.base import BaseCommand
from django.utils import timezone

from poker.advice.engine import BACKFILL_PREFIX
from poker.advice.points import my_decisions
from poker.models import Hand, Recommendation


class Command(BaseCommand):
    help = (
        "Queue analyses for each of my past decisions (finished hands) that has none. "
        "The worker runs them after any other pending analysis."
    )

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int, default=timezone.now().year,
                            help="only hands started that year (default: this year)")
        parser.add_argument("--table", help="only hands at this table (e.g. b17)")
        parser.add_argument("--dry-run", action="store_true", help="list what would be queued")
        parser.add_argument("--redo", action="store_true",
                            help="also re-analyze decisions that already have a finished analysis "
                            "(e.g. after a replay fix); the new one becomes the one shown")

    def handle(self, *args, year, table, dry_run, redo, **opts):
        live_statuses = (Recommendation.PENDING, Recommendation.RUNNING)
        queued = 0
        ts = timezone.now().strftime("%Y%m%d%H%M%S%f")  # keys stay unique if errored ones are retried
        hands = Hand.objects.select_related("table").order_by("table__name", "number")
        if table:
            hands = hands.filter(table__name=table)
        for hand in hands:
            when = hand.started_at or hand.ended_at
            if not hand.is_over or when is None or when.year != year:
                continue
            have = set(hand.recommendations.filter(status__in=live_statuses).values_list("point", flat=True))
            if not redo:
                # Analyses made live carry no actual move, so they can't be graded; redo those too.
                done = hand.recommendations.filter(status=Recommendation.DONE).exclude(actual={})
                have |= set(done.values_list("point", flat=True))
            for d in my_decisions(hand):
                if d.point in have:
                    continue
                self.stdout.write(f"{hand.table.name} hand {hand.number} {d.point}: {d.action.text}")
                if not dry_run:
                    Recommendation.objects.create(
                        hand=hand, point=d.point, decision_key=f"{BACKFILL_PREFIX}{d.point}-{ts}",
                        status=Recommendation.PENDING,
                    )
                queued += 1
        self.stdout.write(f"{'would queue' if dry_run else 'queued'} {queued}")
