from django.core.management.base import BaseCommand, CommandError

from poker.advice import engine
from poker.models import Hand, Recommendation


def summarize(rec, out):
    out(f"{rec}  concurrence={rec.concurrence or '-'}  cost=${rec.cost_usd}")
    for o in rec.opinions:
        amt = f" ${o['amount']}" if o.get("amount") is not None else ""
        out(f"  {o['label']:8} {o['seconds']:>6}s  {o.get('action') or 'ERROR'}{amt}  [{o.get('grade', '')}] {o.get('error', '')}")
        if o.get("reasoning"):
            out(f"           {o['reasoning']}")
    j = rec.judgement
    if j:
        out(f"  judge: preferred={j.get('preferred')} cache_read_tokens={j.get('cache_read_tokens')} {j.get('error', '')}")
        for v in j.get("verdicts", []):
            out(f"    {v['model']}: {v['concurrence']} - {v['evaluation']}")
        out(f"    {j.get('summary', '')}")
    if rec.error:
        out(f"  error: {rec.error}")


class Command(BaseCommand):
    help = "Run advice synchronously for a hand's current decision."

    def add_arguments(self, parser):
        parser.add_argument("table")
        parser.add_argument("hand", type=int)
        parser.add_argument("--force", action="store_true", help="rerun even if already done")

    def handle(self, *args, table, hand, force, **opts):
        try:
            h = Hand.objects.select_related("table").get(table__name=table, number=hand)
        except Hand.DoesNotExist:
            raise CommandError(f"no hand {table} {hand}")
        rec, _ = Recommendation.objects.get_or_create(
            hand=h, decision_key=engine.decision_key(h), defaults={"requested": True}
        )
        if rec.status == Recommendation.DONE and not force:
            self.stdout.write("already done (use --force to rerun)")
        else:
            engine.run(rec)
        rec.refresh_from_db()
        summarize(rec, self.stdout.write)
