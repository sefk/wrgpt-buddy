from django.db import models


class RawEmail(models.Model):
    """Source of truth. Everything else is derived and can be rebuilt."""

    SOURCES = [("eml", "eml file"), ("gmail", "Gmail")]

    message_id = models.CharField(max_length=500, unique=True)
    raw = models.BinaryField()
    source = models.CharField(max_length=10, choices=SOURCES, default="eml")
    gmail_id = models.CharField(max_length=100, null=True, blank=True, unique=True)
    kind = models.CharField(max_length=20, blank=True)
    subject = models.CharField(max_length=500, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    parse_errors = models.TextField(blank=True)
    parsed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["sent_at", "id"]

    def __str__(self):
        return self.subject or self.message_id


class Player(models.Model):
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.name


class Table(models.Model):
    name = models.CharField(max_length=50, unique=True)
    status_url = models.URLField(blank=True)

    def __str__(self):
        return self.name


class HandQuerySet(models.QuerySet):
    def dealt(self):
        """Exclude hand 0: the dealer's pre-game seating phase, where no cards are dealt."""
        return self.exclude(number=0)


class Hand(models.Model):
    table = models.ForeignKey(Table, on_delete=models.CASCADE, related_name="hands")
    number = models.PositiveIntegerField()
    day = models.PositiveIntegerField(null=True, blank=True)
    betting_round = models.PositiveIntegerField(null=True, blank=True)
    board = models.CharField(max_length=20, blank=True, help_text="space-separated cards")
    pot = models.IntegerField(null=True, blank=True)
    to_call = models.IntegerField(null=True, blank=True)
    next_to_act = models.ForeignKey(
        Player, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    next_timeout = models.DateTimeField(null=True, blank=True)
    is_over = models.BooleanField(default=False)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    last_sequence = models.IntegerField(default=-1)
    has_history = models.BooleanField(default=False)
    winner = models.TextField(blank=True)
    objects = HandQuerySet.as_manager()

    sequences = models.JSONField(
        default=list, blank=True, help_text="broadcast sequence numbers received, sorted"
    )

    class Meta:
        unique_together = [("table", "number")]
        ordering = ["table__name", "number"]

    def __str__(self):
        return f"{self.table.name} hand {self.number}"

    def missing_sequences(self) -> list[int]:
        """Broadcasts the dealer sent for this hand that never arrived.

        Sequence numbers are consecutive per table across hands, so gaps inside
        this hand's range are lost mail. A gap before this hand's first broadcast
        counts too when the previous hand is known.
        """
        if not self.sequences:
            return []
        lo, hi = self.sequences[0], self.sequences[-1]
        prev = Hand.objects.filter(table=self.table, number=self.number - 1).first()
        if prev and prev.sequences:
            lo = prev.sequences[-1] + 1
        seen = set(self.sequences)
        return [s for s in range(lo, hi) if s not in seen]


class Seat(models.Model):
    hand = models.ForeignKey(Hand, on_delete=models.CASCADE, related_name="seats")
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="seats")
    seat_number = models.PositiveIntegerField()
    bankroll_start = models.IntegerField(null=True, blank=True)
    bankroll_end = models.IntegerField(null=True, blank=True)
    is_dealer = models.BooleanField(default=False)
    status = models.CharField(max_length=20, blank=True)

    class Meta:
        unique_together = [("hand", "player")]
        ordering = ["seat_number"]


class Action(models.Model):
    hand = models.ForeignKey(Hand, on_delete=models.CASCADE, related_name="actions")
    player = models.ForeignKey(Player, null=True, blank=True, on_delete=models.SET_NULL)
    kind = models.CharField(max_length=30)
    amount = models.IntegerField(null=True, blank=True)
    total = models.IntegerField(null=True, blank=True)
    timestamp = models.DateTimeField(null=True, blank=True)
    betting_round = models.PositiveIntegerField(null=True, blank=True)
    order = models.IntegerField()
    text = models.TextField(blank=True)
    source = models.ForeignKey(
        RawEmail, null=True, blank=True, on_delete=models.CASCADE, related_name="actions"
    )

    class Meta:
        ordering = ["hand_id", "order"]


class MyHand(models.Model):
    hand = models.OneToOneField(Hand, on_delete=models.CASCADE, related_name="mine")
    hole_cards = models.CharField(max_length=5, blank=True)
    game_id = models.CharField(max_length=50, blank=True)
    to_call = models.IntegerField(null=True, blank=True)
    pot = models.IntegerField(null=True, blank=True)
    start_time = models.IntegerField(null=True, blank=True)
    next_timeout = models.DateTimeField(null=True, blank=True)
    private_at = models.DateTimeField(null=True, blank=True)
    last_command = models.CharField(max_length=200, blank=True)
    last_command_at = models.DateTimeField(null=True, blank=True)
    # From the dealer's "Advance action acknowledgement": what it will do for me.
    advance_action = models.TextField(blank=True, help_text="dealer's description, e.g. 'Check if $0 to you, fold otherwise'")
    advance_sticky = models.BooleanField(default=False)
    advance_action_at = models.DateTimeField(null=True, blank=True)
    # From the dealer's "Error Message": my last command was rejected.
    last_error = models.TextField(blank=True)
    last_error_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.hand} {self.hole_cards}"

    @property
    def has_unresolved_error(self) -> bool:
        """An error newer than my last command means I still need to act."""
        if not self.last_error_at:
            return False
        return not self.last_command_at or self.last_error_at > self.last_command_at


class Recommendation(models.Model):
    """Advice for one decision point: local models + Sonnet, then a Sonnet judge turn.

    `opinions` is a list of dicts, one per model, in display order:
        {"model": "qwen/qwen3.6-35b-a3b", "label": "Qwen", "action": "call",
         "amount": 600, "reasoning": "...", "error": "", "seconds": 41.2,
         "grade": "agree" | "same_action_different_size" | "different_action" | ""}
    `grade` compares each local model with Sonnet (computed in code; Sonnet's own is "").
    `judgement` is Sonnet's second turn:
        {"verdicts": [{"model": "Qwen", "concurrence": "...", "evaluation": "..."}],
         "preferred": "Sonnet" | "Qwen" | "gpt-oss", "summary": "...",
         "on_actual": "comment on what I actually did (past points only, else empty)"}
    """

    PENDING, RUNNING, DONE, ERROR = "pending", "running", "done", "error"
    STATUSES = [(s, s) for s in (PENDING, RUNNING, DONE, ERROR)]

    # Hands are deleted and rebuilt by `reparse`; recommendations cost money, so they
    # survive that: the FK nulls out and state.rebuild_all() relinks by the natural key.
    hand = models.ForeignKey(
        Hand, null=True, blank=True, on_delete=models.SET_NULL, related_name="recommendations"
    )
    table_name = models.CharField(max_length=20, blank=True)
    hand_number = models.PositiveIntegerField(null=True, blank=True)
    point = models.CharField(
        max_length=20, blank=True,
        help_text="my decision point: 'r<betting_round>n<k>' = my k-th decision on that street "
        "(see poker.advice.points). Blank for legacy rows.",
    )
    actual = models.JSONField(
        default=dict, blank=True,
        help_text="past points only: what I actually did {action, amount, text}",
    )
    actual_grade = models.CharField(
        max_length=30, blank=True, help_text="my actual move graded against Sonnet"
    )
    decision_key = models.CharField(
        max_length=100, help_text="identifies the decision point, e.g. 'seq10042'"
    )
    requested = models.BooleanField(default=False, help_text="asked for from the UI, not automatic")
    status = models.CharField(max_length=10, choices=STATUSES, default=PENDING)
    prompt = models.TextField(blank=True)
    snapshot = models.JSONField(
        default=dict, blank=True, help_text="state at analysis time: cards, board, pot, to_call"
    )
    opinions = models.JSONField(default=list, blank=True)
    judgement = models.JSONField(default=dict, blank=True)
    steps = models.JSONField(
        default=list, blank=True,
        help_text="progress while running: [{key, label, status: waiting|running|done|error|skipped, "
        "started, finished (ISO), seconds, detail (the model's move), error}]",
    )
    concurrence = models.CharField(
        max_length=30, blank=True,
        help_text="overall: 'agree' if every local model agrees with Sonnet, else the worst grade",
    )
    cost_usd = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    revealed_at = models.DateTimeField(null=True, blank=True, help_text="when I first looked")

    class Meta:
        unique_together = [("table_name", "hand_number", "decision_key")]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.table_name} hand {self.hand_number} {self.point or self.decision_key} {self.status}"

    def save(self, *args, **kwargs):
        if self.hand_id and not self.table_name:
            self.table_name = self.hand.table.name
            self.hand_number = self.hand.number
        super().save(*args, **kwargs)
