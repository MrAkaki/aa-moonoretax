"""Django-admin registration.

Per Requirements §8 all operator workflows live in the front-end tabs, not Django admin.
Models are registered read-only here purely for low-level inspection/debugging.
"""

from django.contrib import admin

from moontax.models import (
    Configuration,
    Extraction,
    Invoice,
    MiningLedger,
    OreTaxRate,
    PaymentContract,
    Structure,
    TokenConfig,
    UnmatchedMiner,
)


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("code", "user", "status", "emitted_at", "paid_at")
    list_filter = ("status",)
    search_fields = ("code",)


@admin.register(Structure)
class StructureAdmin(admin.ModelAdmin):
    list_display = ("structure_id", "name", "has_moon_drilling", "fuel_expires")


@admin.register(Extraction)
class ExtractionAdmin(admin.ModelAdmin):
    list_display = ("structure", "chunk_arrival_time", "fracture_time", "finalized")


for _model in (
    Configuration,
    OreTaxRate,
    TokenConfig,
    MiningLedger,
    UnmatchedMiner,
    PaymentContract,
):
    admin.site.register(_model)
