import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand

from poker.advice import engine
from poker.models import Recommendation

log = logging.getLogger("poker.advice")


class Command(BaseCommand):
    help = "Create due recommendations and process PENDING ones, oldest first, one at a time."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="do one pass and exit")

    def handle(self, *args, once, **opts):
        n = engine.reset_stale()
        if n:
            log.info("reset %d stale running recommendation(s) to pending", n)
        while True:
            self.pass_once()
            if once:
                return
            time.sleep(settings.ADVICE_WORKER_INTERVAL_S)

    def pass_once(self):
        try:
            for rec in engine.due_decisions():
                log.info("due: %s", rec)
            while True:
                rec = Recommendation.objects.filter(status=Recommendation.PENDING).order_by("created_at", "id").first()
                if rec is None:
                    break
                if settings.ADVICE_PAUSE_FILE.exists():
                    log.info("paused for deploy (%s); %s waits", settings.ADVICE_PAUSE_FILE, rec)
                    break
                log.info("running %s", rec)
                engine.run(rec)
                log.info("finished %s concurrence=%s cost=%s", rec, rec.concurrence or "-", rec.cost_usd)
        except Exception:  # noqa: BLE001 - keep the worker alive
            log.exception("worker pass failed")
