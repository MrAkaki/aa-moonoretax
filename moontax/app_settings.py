"""Settings accessors with safe defaults.

Operator-tunable values (per-ore tax rates, finalize/despawn window, reminder cadence,
fuel-low threshold, target corporation) are authoritative on the DB ``Configuration``
model and edited from the Admin tab. The values here are **infrastructure** settings and
**seed defaults** used when a fresh ``Configuration`` row is created — they are not the
runtime authority for operator config.
"""

from decimal import Decimal

from django.conf import settings


def _get(name, default):
    return getattr(settings, name, default)


# --- Infrastructure (not operator-editable) --------------------------------------

# Optional override for the ESI X-Compatibility-Date header. When unset, the providers
# layer pins to ``esi.__esi_compatibility_date__`` (the django-esi build's date).
MOONTAX_ESI_COMPATIBILITY_DATE = _get("MOONTAX_ESI_COMPATIBILITY_DATE", None)

# Days of mining ledger / records to keep and display (relevance window, not an ESI
# guarantee — ESI itself only retains ~30 days).
MOONTAX_DISPLAY_WINDOW_DAYS = int(_get("MOONTAX_DISPLAY_WINDOW_DAYS", 60))

# How far back to walk ESI ledger pages on the first-setup backfill.
MOONTAX_BACKFILL_DAYS = int(_get("MOONTAX_BACKFILL_DAYS", 30))


# --- Seed defaults for a new Configuration row (not the runtime authority) --------

# Default flat per-ore tax rate applied to ore types without an explicit OreTaxRate.
MOONTAX_DEFAULT_TAX_RATE = Decimal(str(_get("MOONTAX_DEFAULT_TAX_RATE", "0.10")))

# Default despawn / finalize window after a pop's fracture, in hours.
MOONTAX_DEFAULT_DESPAWN_HOURS = int(_get("MOONTAX_DEFAULT_DESPAWN_HOURS", 48))

# Default fuel-low highlight threshold, in days.
MOONTAX_DEFAULT_FUEL_LOW_DAYS = int(_get("MOONTAX_DEFAULT_FUEL_LOW_DAYS", 7))

# Reminder cadence anchored on the invoice emit timestamp (UTC):
#   - every N days while the invoice is young,
#   - then daily once the invoice is older than the escalation threshold.
MOONTAX_REMINDER_EVERY_DAYS = int(_get("MOONTAX_REMINDER_EVERY_DAYS", 2))
MOONTAX_REMINDER_DAILY_AFTER_DAYS = int(_get("MOONTAX_REMINDER_DAILY_AFTER_DAYS", 7))

# Default number of rows per page shown in dashboard/staff/admin DataTables.
MOONTAX_DEFAULT_TABLE_PAGE_SIZE = int(_get("MOONTAX_DEFAULT_TABLE_PAGE_SIZE", 25))
