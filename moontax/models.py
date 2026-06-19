"""Database models for moontax.

Schema groups:
- **Config & token:** ``Configuration`` (operator singleton), ``OreTaxRate``,
  ``OreType`` (ESI-sourced moon-ore catalog for the Admin dropdown),
  ``TokenConfig`` (the single corp token).
- **Universe:** ``Moon``, ``Structure``, ``EveName`` (id→name resolution cache).
- **Mining data:** ``MiningLedger`` (cumulative-quantity upsert), ``UnmatchedMiner``,
  ``Extraction`` (a moon pop), ``MoonPopSummary`` (finalized-pop totals snapshot),
  ``ProcessedNotification``.
- **Billing:** ``Invoice`` (+ ``InvoiceItem``, ``InvoiceComment``), ``PaymentContract``.

The permission anchor ``General`` is unmanaged; everything else is a real table covered
by the package's initial migration.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone

from moontax import app_settings
from moontax.managers import (
    EveNameManager,
    InvoiceManager,
    MiningLedgerManager,
    OreTaxRateManager,
    OreTypeManager,
    UnmatchedMinerManager,
)


class General(models.Model):
    """Unmanaged anchor for the plugin's hierarchical permissions.

    Hierarchy (enforced in :mod:`moontax.access`, not by Django itself):
    ``admin_access`` ⊃ ``staff_access`` ⊃ ``basic_access``.
    """

    class Meta:
        managed = False
        default_permissions = ()
        permissions = (
            ("basic_access", "Can access the Moon Ore Tax user dashboard"),
            ("staff_access", "Can access the Moon Ore Tax staff dashboard"),
            (
                "admin_access",
                "Can access the Moon Ore Tax admin tab (token setup & configuration)",
            ),
        )


# --------------------------------------------------------------------------------------
# Config & token
# --------------------------------------------------------------------------------------


class Configuration(models.Model):
    """Operator-tunable config. A single row (pk=1); use :meth:`get_solo`."""

    default_tax_rate = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        default=app_settings.MOONTAX_DEFAULT_TAX_RATE,
        help_text="Flat per-ore tax fraction used when an ore has no explicit OreTaxRate.",
    )
    despawn_hours = models.PositiveIntegerField(
        default=app_settings.MOONTAX_DEFAULT_DESPAWN_HOURS,
        help_text="Hours after fracture before a pop is finalized (ore field despawns).",
    )
    fuel_low_days = models.PositiveIntegerField(
        default=app_settings.MOONTAX_DEFAULT_FUEL_LOW_DAYS,
        help_text="Highlight a structure when fuel remaining is under this many days.",
    )
    reminder_every_days = models.PositiveIntegerField(
        default=app_settings.MOONTAX_REMINDER_EVERY_DAYS,
        help_text="Reminder interval (days) while an unpaid invoice is still young.",
    )
    reminder_daily_after_days = models.PositiveIntegerField(
        default=app_settings.MOONTAX_REMINDER_DAILY_AFTER_DAYS,
        help_text="Once an unpaid invoice is older than this many days, remind daily.",
    )
    table_page_size = models.PositiveIntegerField(
        default=app_settings.MOONTAX_DEFAULT_TABLE_PAGE_SIZE,
        help_text="Default number of rows per page in dashboard/staff/admin tables.",
    )
    target_corporation_id = models.BigIntegerField(null=True, blank=True)
    target_corporation_name = models.CharField(max_length=255, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "configuration"

    def __str__(self) -> str:
        return f"Moon Ore Tax configuration (corp {self.target_corporation_id})"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls) -> "Configuration":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class OreTaxRate(models.Model):
    """Per-ore-type tax-rate override. Absent ⇒ ``Configuration.default_tax_rate``."""

    ore_type_id = models.BigIntegerField(unique=True)
    ore_type_name = models.CharField(max_length=100, blank=True)
    rate = models.DecimalField(max_digits=5, decimal_places=4)

    objects = OreTaxRateManager()

    class Meta:
        ordering = ["ore_type_name", "ore_type_id"]

    def __str__(self) -> str:
        return f"{self.ore_type_name or self.ore_type_id}: {self.rate}"


class OreType(models.Model):
    """A moon ore (base or quality/compressed variant) in the ESI-sourced catalog.

    The catalog is populated from public ESI (the five moon-asteroid groups in
    :data:`moontax.ores.MOON_ORE_GROUP_IDS`) by ``tasks.load_ore_catalog`` and the
    ``moontax_load_ores`` setup command — never typed in.  Base ores have
    ``base_type_id=None``; quality/compressed variants point at their base ore via
    ``base_type_id``.  The Admin dropdown only shows base ores; the tax engine uses
    :meth:`OreTypeManager.effective_type_id` to resolve a variant to its base before
    rate lookup.
    """

    type_id = models.BigIntegerField(unique=True)
    name = models.CharField(max_length=100, blank=True)
    group_id = models.BigIntegerField(null=True, blank=True)
    base_type_id = models.BigIntegerField(
        null=True,
        blank=True,
        help_text=(
            "For a quality/compressed variant, the type_id of its base moon ore; "
            "null for a base ore."
        ),
    )

    objects = OreTypeManager()

    class Meta:
        # group_id ascending == ubiquitous → exceptional (see moontax.ores).
        ordering = ["group_id", "name"]

    def __str__(self) -> str:
        return self.name or str(self.type_id)


class TokenConfig(models.Model):
    """The single corp ESI token (added by a Director/CEO). One row (pk=1)."""

    token = models.ForeignKey(
        "esi.Token", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    character_id = models.BigIntegerField(null=True, blank=True)
    character_name = models.CharField(max_length=255, blank=True)
    corporation_id = models.BigIntegerField(null=True, blank=True)
    corporation_name = models.CharField(max_length=255, blank=True)
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    is_valid = models.BooleanField(default=True)
    last_validated = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "token configuration"

    def __str__(self) -> str:
        return f"Corp token: {self.character_name or 'unset'} ({self.corporation_name})"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls) -> "TokenConfig | None":
        return cls.objects.filter(pk=1).first()


# --------------------------------------------------------------------------------------
# Universe
# --------------------------------------------------------------------------------------


class EveName(models.Model):
    """Tiny id→name resolution cache (ore types, moons, systems, characters, corps)."""

    ORE = "ore_type"
    MOON = "moon"
    SYSTEM = "system"
    CHARACTER = "character"
    CORPORATION = "corporation"
    STRUCTURE_TYPE = "structure_type"

    eve_id = models.BigIntegerField(primary_key=True)
    category = models.CharField(max_length=32, blank=True)
    name = models.CharField(max_length=255, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = EveNameManager()

    def __str__(self) -> str:
        return self.name or str(self.eve_id)


class Moon(models.Model):
    moon_id = models.BigIntegerField(primary_key=True)
    name = models.CharField(max_length=255, blank=True)
    system_id = models.BigIntegerField(null=True, blank=True)
    system_name = models.CharField(max_length=255, blank=True)

    def __str__(self) -> str:
        return self.name or f"Moon {self.moon_id}"


class Structure(models.Model):
    """A corp moon-mining structure (Athanor/Tatara)."""

    structure_id = models.BigIntegerField(primary_key=True)
    name = models.CharField(max_length=255, blank=True)
    corporation_id = models.BigIntegerField(null=True, blank=True)
    system_id = models.BigIntegerField(null=True, blank=True)
    system_name = models.CharField(max_length=255, blank=True)
    type_id = models.BigIntegerField(null=True, blank=True)
    type_name = models.CharField(max_length=255, blank=True)
    moon = models.ForeignKey(
        Moon, on_delete=models.SET_NULL, null=True, blank=True, related_name="structures"
    )
    fuel_expires = models.DateTimeField(null=True, blank=True)
    has_moon_drilling = models.BooleanField(default=False)
    drill_state = models.CharField(max_length=32, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "structure_id"]

    def __str__(self) -> str:
        return self.name or f"Structure {self.structure_id}"

    @property
    def fuel_days_remaining(self) -> float | None:
        if not self.fuel_expires:
            return None
        delta = self.fuel_expires - timezone.now()
        return delta.total_seconds() / 86400.0

    def is_fuel_low(self, threshold_days: int) -> bool:
        days = self.fuel_days_remaining
        return days is not None and days < threshold_days

    @property
    def drill_set_up(self) -> bool:
        """A "Moon Drilling" service exists and is online."""
        return self.has_moon_drilling and self.drill_state.lower() == "online"

    @property
    def next_extraction(self) -> "Extraction | None":
        """Soonest not-yet-fractured extraction, by chunk arrival."""
        return (
            self.extractions.filter(fracture_time__isnull=True)
            .order_by("chunk_arrival_time")
            .first()
        )


# --------------------------------------------------------------------------------------
# Mining data
# --------------------------------------------------------------------------------------


class MiningLedger(models.Model):
    """One observer-ledger cell. ``quantity`` is cumulative-to-date for the key, so each
    poll **overwrites** it — never sum across polls (see :class:`MiningLedgerManager`)."""

    observer_id = models.BigIntegerField()
    character_id = models.BigIntegerField()
    ore_type_id = models.BigIntegerField()
    recorded_date = models.DateField(help_text="Ledger day (UTC), derived from last_updated.")
    quantity = models.BigIntegerField(default=0)
    structure = models.ForeignKey(
        Structure,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ledger_rows",
    )
    recorded_corporation_id = models.BigIntegerField(null=True, blank=True)
    last_updated = models.DateField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = MiningLedgerManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["observer_id", "character_id", "ore_type_id", "recorded_date"],
                name="moontax_ledger_uniq",
            )
        ]
        indexes = [
            models.Index(fields=["recorded_date"]),
            models.Index(fields=["character_id", "recorded_date"]),
            models.Index(fields=["observer_id", "recorded_date"]),
        ]

    def __str__(self) -> str:
        return (
            f"obs {self.observer_id} char {self.character_id} "
            f"ore {self.ore_type_id} {self.recorded_date}: {self.quantity}"
        )


class UnmatchedMiner(models.Model):
    """Ledger ore by a character not linked to any AA user (record-keeping only)."""

    observer_id = models.BigIntegerField()
    character_id = models.BigIntegerField()
    character_name = models.CharField(max_length=255, blank=True)
    ore_type_id = models.BigIntegerField()
    recorded_date = models.DateField()
    quantity = models.BigIntegerField(default=0)
    structure = models.ForeignKey(
        Structure, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)

    objects = UnmatchedMinerManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["observer_id", "character_id", "ore_type_id", "recorded_date"],
                name="moontax_unmatched_uniq",
            )
        ]
        indexes = [models.Index(fields=["recorded_date"])]

    def __str__(self) -> str:
        return f"unmatched char {self.character_id} {self.recorded_date}: {self.quantity}"


class Extraction(models.Model):
    """A single moon pop (chunk): scheduled from ``MoonminingExtractionStarted``, then
    fractured by a laser/auto notification, then finalized after the despawn window."""

    LASER = "laser"
    AUTO = "auto"
    FRACTURE_CHOICES = [(LASER, "Manual (laser)"), (AUTO, "Automatic")]

    structure = models.ForeignKey(
        Structure, on_delete=models.CASCADE, related_name="extractions"
    )
    moon = models.ForeignKey(
        Moon, on_delete=models.SET_NULL, null=True, blank=True, related_name="extractions"
    )
    chunk_arrival_time = models.DateTimeField(
        help_text="readyTime — chunk ready to fracture; anchors attribution windows."
    )
    auto_fracture_time = models.DateTimeField(
        null=True, blank=True, help_text="autoTime — natural fracture."
    )
    ore_volume_by_type = models.JSONField(
        default=dict, blank=True, help_text="oreVolumeByType: {type_id: volume} chunk total."
    )
    started_notification_id = models.BigIntegerField(null=True, blank=True)
    fracture_time = models.DateTimeField(
        null=True, blank=True, help_text="The pop: when the chunk actually fractured."
    )
    fracture_type = models.CharField(max_length=8, blank=True, choices=FRACTURE_CHOICES)
    fracture_notification_id = models.BigIntegerField(null=True, blank=True)
    finalized = models.BooleanField(default=False)
    finalized_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["structure", "chunk_arrival_time"], name="moontax_extraction_uniq"
            )
        ]
        ordering = ["-chunk_arrival_time"]

    def __str__(self) -> str:
        return f"{self.structure} pop @ {self.chunk_arrival_time:%Y-%m-%d}"

    def finalize_due_at(self, despawn_hours: int):
        """When the pop is ready to finalize: ``fracture_time + despawn_hours``."""
        if not self.fracture_time:
            return None
        return self.fracture_time + timezone.timedelta(hours=despawn_hours)

    def is_ready_to_finalize(self, despawn_hours: int) -> bool:
        due = self.finalize_due_at(despawn_hours)
        return due is not None and timezone.now() >= due


class MoonPopSummary(models.Model):
    """Snapshot of a finalized moon pop ("moon death"): totals computed once when the
    pop is finalized in tax.finalize_pop. ore_mined_units and expected_total_taxes are
    death-time facts; invoices_paid is read live (payments arrive after death)."""

    extraction = models.OneToOneField(
        Extraction, on_delete=models.CASCADE, related_name="summary"
    )
    structure = models.ForeignKey(
        Structure, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    moon = models.ForeignKey(
        Moon, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    ore_mined_units = models.BigIntegerField(
        default=0,
        help_text="Total ore units mined in the pop window (linked + unlinked miners).",
    )
    expected_total_taxes = models.BigIntegerField(
        default=0,
        help_text="Total ore units owed across all invoices emitted for this pop.",
    )
    invoices_emitted = models.PositiveIntegerField(default=0)
    finalized_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-finalized_at"]

    def __str__(self) -> str:
        return f"Pop summary for {self.extraction}"

    @property
    def invoices_paid(self) -> int:
        # Live count — payments land after the death-time snapshot.
        return self.extraction.invoices.filter(status__in=Invoice.PAID_STATUSES).count()


class ProcessedNotification(models.Model):
    """Process-once guard for corp notifications (keyed by ESI notification_id)."""

    notification_id = models.BigIntegerField(primary_key=True)
    notification_type = models.CharField(max_length=64, blank=True)
    timestamp = models.DateTimeField(null=True, blank=True)
    processed_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.notification_type} #{self.notification_id}"


# --------------------------------------------------------------------------------------
# Billing
# --------------------------------------------------------------------------------------


class Invoice(models.Model):
    """One invoice per (player, pop). Paid in ore via an in-game contract."""

    EMITTED = "emitted"
    PAYMENT_SENT = "payment_sent"
    PAYMENT_ACCEPTED = "payment_accepted"
    MARKED_PAID = "marked_paid"
    CONDONED = "condoned"
    STATUS_CHOICES = [
        (EMITTED, "Emitted"),
        (PAYMENT_SENT, "Payment sent"),
        (PAYMENT_ACCEPTED, "Payment accepted"),
        (MARKED_PAID, "Marked paid"),
        (CONDONED, "Condoned"),
    ]
    PAID_STATUSES = frozenset({PAYMENT_ACCEPTED, MARKED_PAID, CONDONED})

    code = models.CharField(max_length=32, unique=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="moontax_invoices"
    )
    extraction = models.ForeignKey(
        Extraction, on_delete=models.CASCADE, related_name="invoices"
    )
    structure = models.ForeignKey(
        Structure, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    moon = models.ForeignKey(
        Moon, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=EMITTED)
    emitted_at = models.DateTimeField(default=timezone.now)
    paid_at = models.DateTimeField(null=True, blank=True)
    last_reminder_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = InvoiceManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "extraction"], name="moontax_invoice_user_pop_uniq"
            )
        ]
        ordering = ["-emitted_at"]

    def __str__(self) -> str:
        return f"Invoice {self.code} ({self.get_status_display()})"

    @property
    def is_paid(self) -> bool:
        return self.status in self.PAID_STATUSES

    @property
    def total_units(self) -> int:
        return sum(item.units_owed for item in self.items.all())

    @property
    def resolution_type(self) -> str | None:
        return {
            self.PAYMENT_ACCEPTED: "Paid by user",
            self.MARKED_PAID: "Marked paid by staff",
            self.CONDONED: "Condoned by staff",
        }.get(self.status)

    def regenerate_code(self, save: bool = True) -> str:
        self.code = Invoice.objects.generate_code()
        if save:
            self.save(update_fields=["code", "updated_at"])
        return self.code


class InvoiceItem(models.Model):
    """Owed units for one ore type on an invoice."""

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="items")
    ore_type_id = models.BigIntegerField()
    ore_type_name = models.CharField(max_length=100, blank=True)
    units_owed = models.BigIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["invoice", "ore_type_id"], name="moontax_invoiceitem_uniq"
            )
        ]

    def __str__(self) -> str:
        return f"{self.units_owed} x {self.ore_type_name or self.ore_type_id}"

    @property
    def compressed_alternative(self) -> dict | None:
        """The "or pay compressed" option for this line, or None if there isn't one.

        Returns ``{"type_id", "units", "name"}`` for the compressed equivalent (units
        rounded down). None when the ore has no compressed form or the rounded-down
        amount is zero (a line owing under one compressed unit must be paid in raw).
        """
        from moontax.core import compression

        comp_id = OreType.objects.compressed_type_id(self.ore_type_id)
        if not comp_id:
            return None
        units = compression.compressed_units(self.units_owed)
        if units <= 0:
            return None
        name = (
            OreType.objects.filter(type_id=comp_id).values_list("name", flat=True).first()
            or f"Compressed {self.ore_type_name}".strip()
        )
        return {"type_id": comp_id, "units": units, "name": name}


class InvoiceComment(models.Model):
    """Audit entry for a staff action on an invoice (mark paid / condone / delete payment)."""

    MARK_PAID = "mark_paid"
    CONDONE = "condone"
    DELETE_PAYMENT = "delete_payment"
    NOTE = "note"
    ACTION_CHOICES = [
        (MARK_PAID, "Marked paid"),
        (CONDONE, "Condoned"),
        (DELETE_PAYMENT, "Deleted payment"),
        (NOTE, "Note"),
    ]

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="comments")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    action = models.CharField(max_length=20, choices=ACTION_CHOICES, default=NOTE)
    comment = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.get_action_display()} on {self.invoice.code}"


# --------------------------------------------------------------------------------------
# User notification preferences
# --------------------------------------------------------------------------------------


class NotificationSetting(models.Model):
    """Per-user opt-in flags for non-critical notifications. Missing row ⇒ all enabled
    (defaults True). Overdue-invoice reminders are always sent and are NOT gated here."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="moontax_notification_setting",
    )
    moon_pop = models.BooleanField(
        default=True,
        help_text="Notify when a new moon extraction is scheduled.",
    )
    moon_dead = models.BooleanField(
        default=True,
        help_text="Notify when a moon pop is finalized / the ore field despawns.",
    )
    invoice_emitted = models.BooleanField(
        default=True,
        help_text="Notify when a new tax invoice is emitted to you.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Notification settings for {self.user}"


class PaymentContract(models.Model):
    """An ESI corp contract we ingested; linked to an invoice once it matches (§6)."""

    contract_id = models.BigIntegerField(primary_key=True)
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_contracts",
    )
    contract_type = models.CharField(max_length=32, blank=True)
    status = models.CharField(max_length=32, blank=True)
    issuer_id = models.BigIntegerField(null=True, blank=True)
    issuer_name = models.CharField(max_length=255, blank=True)
    assignee_id = models.BigIntegerField(null=True, blank=True)
    title = models.CharField(max_length=255, blank=True)
    price = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    reward = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    volume = models.FloatField(null=True, blank=True)
    location_id = models.BigIntegerField(null=True, blank=True)
    location_name = models.CharField(max_length=255, blank=True)
    date_issued = models.DateTimeField(null=True, blank=True)
    date_completed = models.DateTimeField(null=True, blank=True)
    # Offered items (is_included == true): [{"type_id": int, "quantity": int}, ...]
    offered_items = models.JSONField(default=list, blank=True)
    has_requested_items = models.BooleanField(default=False)
    items_fetched = models.BooleanField(default=False)
    last_mismatch_notified_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date_issued"]

    def __str__(self) -> str:
        return f"Contract {self.contract_id} ({self.status})"
