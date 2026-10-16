from django.core.management.base import BaseCommand

from poker.advice import replay
from poker.models import Hand


class Command(BaseCommand):
    help = "Check replay's chip accounting: pot at every street boundary vs the dealer's 'Pot right', and stacks."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="include hands without full history")
        parser.add_argument("-v2", dest="detail", action="store_true", help="list every mismatch")

    def handle(self, *args, all, detail, **opts):
        hands = Hand.objects.dealt().select_related("table")
        if not all:
            hands = hands.filter(has_history=True)
        nb = nb_ok = ns = ns_ok = nh = nh_ok = 0
        for h in hands:
            nh += 1
            bad = []
            for rnd, dealer, ours in replay.validate(h):
                nb += 1
                if dealer == ours:
                    nb_ok += 1
                else:
                    bad.append(f"round {rnd}: dealer pot {dealer}, replay {ours}")
            if h.has_history and h.is_over:
                for name, a, b in replay.validate_stacks(h):
                    ns += 1
                    if a == b:
                        ns_ok += 1
                    else:
                        bad.append(f"stack {name}: replay start {a}, from bankroll_end {b}")
            if not bad:
                nh_ok += 1
            elif detail:
                self.stdout.write(f"{h}: " + "; ".join(bad))
        self.stdout.write(f"hands {nh_ok}/{nh} fully consistent; street pots {nb_ok}/{nb}; start stacks {ns_ok}/{ns}")
