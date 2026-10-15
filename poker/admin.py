from django.contrib import admin

from . import models

for m in (models.Player, models.Table, models.Hand, models.Seat, models.Action, models.MyHand):
    admin.site.register(m)


@admin.register(models.RawEmail)
class RawEmailAdmin(admin.ModelAdmin):
    list_display = ("subject", "kind", "sent_at", "source", "parse_errors")
    list_filter = ("kind", "source")
    exclude = ("raw",)
    readonly_fields = ("received_at",)
