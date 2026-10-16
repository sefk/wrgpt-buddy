from django.core.management.base import BaseCommand, CommandError

from poker.advice import engine
from poker.advice.points import live_point, my_decisions
from poker.models import Hand, Recommendation


def summarize(rec, out):
    out(f"{rec}  concurrence={rec.concurrence or '-'}  cost=${rec.cost_usd}")
    if rec.actual:
        a = rec.actual
        amt = f" ${a['amount']}" if a.get("amount") is not None else ""
        out(f"  I played: {a.get('text')}  ({a.get('action')}{amt})  actual_grade={rec.actual_grade or '-'}")
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
        if j.get("on_actual"):
            out(f"    on my actual move: {j['on_actual']}")
    if rec.error:
        out(f"  error: {rec.error}")


class Command(BaseCommand):
    help = "Run advice synchronously for a hand's current decision."

    def add_arguments(self, parser):
        parser.add_argument("table")
        parser.add_argument("hand", type=int)
        parser.add_argument("--force", action="store_true", help="rerun even if already done")
        parser.add_argument("--point", help="analyze one past decision of mine, e.g. r1n1")
        parser.add_argument("--all-points", action="store_true", help="analyze each of my past decisions in the hand")

    def handle(self, *args, table, hand, force, point, all_points, **opts):
        try:
            h = Hand.objects.select_related("table").get(table__name=table, number=hand)
        except Hand.DoesNotExist:
            raise CommandError(f"no hand {table} {hand}")
        decisions = {d.point for d in my_decisions(h)}
        if point and all_points:
            raise CommandError("use --point or --all-points, not both")
        if point:
            if point not in decisions:
                raise CommandError(f"no decision {point!r} for me in {h}; have {sorted(decisions)}")
            self.past(h, point)
            return
        if all_points:
            for d in my_decisions(h):
                done = Recommendation.objects.filter(hand=h, point=d.point, decision_key__startswith="pt-",
                                                     status=Recommendation.DONE).exists()
                if done and not force:
                    self.stdout.write(f"{d.point}: already done (use --force to rerun)")
                    continue
                self.past(h, d.point)
            return
        lp = live_point(h)
        rec, _ = Recommendation.objects.get_or_create(
            hand=h, decision_key=engine.decision_key(h),
            defaults={"requested": True, "point": lp.point if lp else ""},
        )
        if rec.status == Recommendation.DONE and not force:
            self.stdout.write("already done (use --force to rerun)")
        else:
            engine.run(rec)
        rec.refresh_from_db()
        summarize(rec, self.stdout.write)

    def past(self, hand, point):
        rec = Recommendation.objects.create(
            hand=hand, point=point, decision_key=engine.past_decision_key(point), requested=True
        )
        engine.run(rec)
        rec.refresh_from_db()
        summarize(rec, self.stdout.write)
