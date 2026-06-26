"""Front-end views for the three permission-gated tabs (Requirements §8).

All workflows live here — there are no Django-admin workflows.
"""

from __future__ import annotations

import datetime as dt

from django.contrib import messages
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from allianceauth.authentication.models import CharacterOwnership
from esi.decorators import token_required

from moontax import providers
from moontax.access import (
    access_level,
    admin_access_required,
    basic_access_required,
    can_admin,
    can_staff,
    staff_access_required,
)
from moontax.core import matching, tax
from moontax.forms import ConfigurationForm, NotificationSettingForm, OreTaxRateForm, StaffActionForm
from moontax.models import (
    Configuration,
    Extraction,
    Invoice,
    InvoiceComment,
    MiningLedger,
    MoonPopSummary,
    NotificationSetting,
    OreTaxRate,
    OreType,
    Structure,
    TokenConfig,
    UnmatchedMiner,
)


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def _main_char_name(user) -> str:
    try:
        main = user.profile.main_character
        if main:
            return main.character_name
    except Exception:  # noqa: BLE001 - profile may be missing in odd states
        pass
    return user.username


def _next_pop_label(structure: Structure):
    """Human label for a structure's next pop (days, or hours if < 1 day)."""
    ext = structure.next_extraction
    if ext is None:
        return "none scheduled", None
    delta = ext.chunk_arrival_time - timezone.now()
    secs = delta.total_seconds()
    if secs <= 0:
        return "now / overdue", ext.chunk_arrival_time
    days = secs / 86400.0
    if days < 1:
        return f"{secs / 3600.0:.0f}h", ext.chunk_arrival_time
    return f"{days:.1f}d", ext.chunk_arrival_time


def _expected_ore_list(extraction) -> list[dict]:
    """Return [{name, amount}] sorted by amount desc for the given extraction's chunk.

    ``ore_volume_by_type`` keys are strings (JSON), so we cast to int for name lookup.
    Returns [] when the extraction is None or the dict is empty.
    """
    if extraction is None:
        return []
    raw = extraction.ore_volume_by_type  # {str(type_id): volume}
    if not raw:
        return []
    # Cast string JSON keys to int for the shared name resolver.
    int_ids = {int(k): v for k, v in raw.items() if v}
    if not int_ids:
        return []
    name_map = tax.resolve_ore_names(set(int_ids))
    result = [
        {"name": name_map.get(tid, str(tid)), "amount": vol}
        for tid, vol in int_ids.items()
    ]
    result.sort(key=lambda x: x["amount"], reverse=True)
    return result


def _structure_rows(config: Configuration):
    rows = []
    # Include only moon-drilling structures: those flagged as moon drills or
    # already associated with a moon (handles drills whose service is offline).
    qs = Structure.objects.select_related("moon").filter(
        Q(has_moon_drilling=True) | Q(moon__isnull=False)
    )
    for s in qs:
        label, when = _next_pop_label(s)
        fuel_days = s.fuel_days_remaining
        ext = s.next_extraction
        rows.append(
            {
                "structure": s,
                "moon": s.moon,
                "fuel_days": fuel_days,
                "next_pop_label": label,
                "next_pop_at": when,
                # Numeric sort key for the "Next pop" column (DataTables data-order):
                # soonest first; structures with no scheduled pop sort last.
                "next_pop_sort": int(when.timestamp()) if when else 9999999999,
                "fuel_low": s.is_fuel_low(config.fuel_low_days),
                "drill_set_up": s.drill_set_up,
                "warn": s.is_fuel_low(config.fuel_low_days) or not s.drill_set_up,
                # Expected ore from the upcoming chunk composition.
                "expected_ore": _expected_ore_list(ext),
            }
        )
    return rows


# --------------------------------------------------------------------------------------
# User dashboard
# --------------------------------------------------------------------------------------


def _build_pop_data(user):
    """Return ``(pop_charts, mined_pops)`` for the user dashboard.

    Drives both the per-pop pie chart and the "What you mined (last 60 days)"
    table from a single extraction loop, reusing tax._attribution_window for
    attribution. Ore names are resolved in bulk: OreType catalog first, then
    EveName, then the raw type_id string as a last resort.

    ``pop_charts`` covers the last 180 days (pie chart window).
    ``mined_pops`` covers only the last 60 days (table window).
    """
    cutoff = timezone.now().date() - dt.timedelta(days=60)
    char_ids = matching.character_ids_for_user(user)

    pop_charts: list[dict] = []
    mined_pops: list[dict] = []

    if not char_ids:
        return pop_charts, mined_pops

    # Build a character_id → character_name map for the logged-in user.
    ownerships = CharacterOwnership.objects.filter(user=user).select_related("character")
    char_name_map = {co.character.character_id: co.character.character_name for co in ownerships}

    ext_cutoff = timezone.now() - dt.timedelta(days=180)
    extractions = Extraction.objects.select_related("structure", "moon").filter(
        chunk_arrival_time__gte=ext_cutoff
    ).order_by("-chunk_arrival_time")

    # First pass: collect per-pop aggregations and accumulate ore ids for bulk lookup.
    raw_pops = []  # (extraction, by_char_rows, by_ore_rows, in_table_window)
    all_ore_ids: set[int] = set()

    for extraction in extractions:
        start, end = tax._attribution_window(extraction)
        qs = MiningLedger.objects.filter(
            observer_id=extraction.structure_id,
            character_id__in=char_ids,
            recorded_date__gte=start,
        )
        if end is not None:
            qs = qs.filter(recorded_date__lt=end)

        # Per-character aggregation (pie chart).
        by_char = list(qs.values("character_id").annotate(units=Sum("quantity")))
        # Per-ore aggregation (table).
        by_ore = list(qs.values("ore_type_id").annotate(units=Sum("quantity")))

        has_mining = any(r["units"] and r["units"] > 0 for r in by_char)
        if not has_mining:
            continue  # user did not mine this pop

        pop_date = extraction.chunk_arrival_time.date()
        in_table_window = pop_date >= cutoff

        all_ore_ids.update(r["ore_type_id"] for r in by_ore if r["units"] and r["units"] > 0)
        raw_pops.append((extraction, by_char, by_ore, in_table_window))

    # Build ore name map in bulk via the shared helper: OreType → EveName → str(id).
    ore_name_map = tax.resolve_ore_names(all_ore_ids)

    # Second pass: build output dicts.
    for extraction, by_char, by_ore, in_table_window in raw_pops:
        moon_name = str(extraction.moon) if extraction.moon else ""

        # Pie chart entry (all pops within 180-day window).
        labels = []
        data = []
        for row in by_char:
            if row["units"] and row["units"] > 0:
                cid = row["character_id"]
                labels.append(char_name_map.get(cid, str(cid)))
                data.append(row["units"])

        pop_charts.append({
            "structure": str(extraction.structure),
            "moon": moon_name,
            "date": extraction.chunk_arrival_time.strftime("%Y-%m-%d %H:%M"),
            "labels": labels,
            "data": data,
        })

        # Table entry (only pops within the 60-day window).
        if in_table_window:
            ores = []
            total_units = 0
            for row in by_ore:
                if row["units"] and row["units"] > 0:
                    tid = row["ore_type_id"]
                    ores.append({
                        "name": ore_name_map.get(tid, str(tid)),
                        "units": row["units"],
                    })
                    total_units += row["units"]
            if ores:
                mined_pops.append({
                    "date": extraction.chunk_arrival_time.strftime("%Y-%m-%d %H:%M"),
                    "structure": str(extraction.structure),
                    "moon": moon_name,
                    "ores": ores,
                    "total_units": total_units,
                })

    return pop_charts, mined_pops


@basic_access_required
def index(request):
    config = Configuration.get_solo()
    user = request.user
    invoices = (
        Invoice.objects.filter(user=user)
        .prefetch_related("items")
        .select_related("structure", "moon")
    )
    unpaid = [i for i in invoices if not i.is_paid]
    # Resolve-and-persist any blank/numeric ore names so the unpaid-invoices alert
    # shows ore names, not type ids (heals invoices emitted before the catalog filled).
    for inv in invoices:
        tax.heal_invoice_item_names(inv.items.all())

    pop_charts, mined_pops = _build_pop_data(user)

    context = {
        "access_level": access_level(user),
        "can_staff": can_staff(user),
        "can_admin": can_admin(user),
        "unpaid_invoices": unpaid,
        "all_invoices": list(invoices),
        "structures": _structure_rows(config),
        "mined_pops": mined_pops,
        "table_page_size": config.table_page_size,
        "pop_charts": pop_charts,
    }
    return render(request, "moontax/dashboard.html", context)


# --------------------------------------------------------------------------------------
# Notification preferences
# --------------------------------------------------------------------------------------


@basic_access_required
def notification_settings(request):
    setting, _ = NotificationSetting.objects.get_or_create(user=request.user)
    if request.method == "POST":
        form = NotificationSettingForm(request.POST, instance=setting)
        if form.is_valid():
            form.save()
            messages.success(request, "Notification preferences saved.")
            return redirect("moontax:notifications")
    else:
        form = NotificationSettingForm(instance=setting)
    context = {
        "can_staff": can_staff(request.user),
        "can_admin": can_admin(request.user),
        "form": form,
    }
    return render(request, "moontax/notifications.html", context)


# --------------------------------------------------------------------------------------
# Staff dashboard
# --------------------------------------------------------------------------------------


@staff_access_required
def staff(request):
    config = Configuration.get_solo()
    invoices = Invoice.objects.select_related("user", "structure", "moon").prefetch_related(
        "items", "comments", "payment_contracts"
    )

    ready = [i for i in invoices if i.status == Invoice.PAYMENT_SENT]
    unpaid = [i for i in invoices if i.status == Invoice.EMITTED]
    paid = [i for i in invoices if i.is_paid]

    for i in list(ready) + list(unpaid) + list(paid):
        i.main_char = _main_char_name(i.user)
        i.days_since_emitted = (timezone.now() - i.emitted_at).days
        # Resolve-and-persist blank/numeric ore names for the staff invoice tables.
        tax.heal_invoice_item_names(i.items.all())

    cutoff = timezone.now().date() - dt.timedelta(days=60)
    unmatched_records = list(
        UnmatchedMiner.objects.filter(recorded_date__gte=cutoff).order_by("-recorded_date")
    )
    # Resolve ore names: OreType catalog → EveName → str(type_id).
    unmatched_ore_names = tax.resolve_ore_names({u.ore_type_id for u in unmatched_records})
    # Aggregate per character: one row listing everything they mined, units summed
    # per ore type, keeping the most recent recorded date as "last seen".
    unmatched_by_char: dict[int, dict] = {}
    for u in unmatched_records:
        entry = unmatched_by_char.get(u.character_id)
        if entry is None:
            entry = {
                "character_id": u.character_id,
                "character_name": u.character_name,
                "last_date": u.recorded_date,
                "ore_totals": {},  # ore_type_id → summed units
            }
            unmatched_by_char[u.character_id] = entry
        entry["ore_totals"][u.ore_type_id] = (
            entry["ore_totals"].get(u.ore_type_id, 0) + u.quantity
        )
        if u.recorded_date > entry["last_date"]:
            entry["last_date"] = u.recorded_date
        if not entry["character_name"] and u.character_name:
            entry["character_name"] = u.character_name

    unmatched = []
    for entry in unmatched_by_char.values():
        ores = [
            {"name": unmatched_ore_names.get(tid, str(tid)), "units": units}
            for tid, units in entry["ore_totals"].items()
        ]
        ores.sort(key=lambda o: o["units"], reverse=True)
        unmatched.append({
            "character_id": entry["character_id"],
            "character_name": entry["character_name"],
            "ores": ores,
            "last_date": entry["last_date"],
        })
    # Most recently active characters first.
    unmatched.sort(key=lambda c: c["last_date"], reverse=True)

    # Active (not yet finalized) pops for the "Active pops" table.
    active_extractions = (
        Extraction.objects.filter(finalized=False)
        .select_related("structure", "moon")
        .order_by("chunk_arrival_time")
    )
    active_pops = []
    for ext in active_extractions:
        active_pops.append({
            "extraction": ext,
            "structure": ext.structure,
            "moon": ext.moon,
            "chunk_arrival_time": ext.chunk_arrival_time,
            "expected_ore": _expected_ore_list(ext),
        })

    moon_pops = MoonPopSummary.objects.select_related(
        "structure", "moon", "extraction"
    ).all()

    # Build per-pop ore breakdown dict for the modal: str(pop.pk) → [{"name", "units"}, ...]
    # Bulk-collect all ore type ids across all pops first, then do a single OreType + EveName
    # lookup, and finally resolve names when assembling the breakdown per pop.
    pop_ore_details: dict[str, list[dict]] = {}
    for pop in moon_pops:
        breakdown = tax.pop_ore_breakdown(pop.extraction)
        pop_ore_details[str(pop.pk)] = [
            {"name": entry["name"], "units": entry["units"]} for entry in breakdown
        ]

    context = {
        "can_staff": True,
        "can_admin": can_admin(request.user),
        "ready_to_accept": ready,
        "unpaid_invoices": unpaid,
        "paid_invoices": paid,
        "structures": _structure_rows(config),
        "unmatched": unmatched,
        "moon_pops": moon_pops,
        "pop_ore_details": pop_ore_details,
        "active_pops": active_pops,
        "action_form": StaffActionForm(),
        "table_page_size": config.table_page_size,
    }
    return render(request, "moontax/staff.html", context)


@staff_access_required
def staff_action(request, invoice_id):
    """Mark paid / condone / delete payment — each requires a comment."""
    if request.method != "POST":
        return redirect("moontax:staff")
    invoice = get_object_or_404(Invoice, pk=invoice_id)
    action = request.POST.get("action")
    form = StaffActionForm(request.POST)
    if not form.is_valid():
        messages.error(request, "A comment is required for this action.")
        return redirect("moontax:staff")
    comment = form.cleaned_data["comment"]
    now = timezone.now()

    if action == "mark_paid":
        invoice.status = Invoice.MARKED_PAID
        invoice.paid_at = now
        invoice.save(update_fields=["status", "paid_at", "updated_at"])
        InvoiceComment.objects.create(
            invoice=invoice, user=request.user, action=InvoiceComment.MARK_PAID, comment=comment
        )
        messages.success(request, f"Invoice {invoice.code} marked paid.")
    elif action == "condone":
        invoice.status = Invoice.CONDONED
        invoice.paid_at = now
        invoice.save(update_fields=["status", "paid_at", "updated_at"])
        InvoiceComment.objects.create(
            invoice=invoice, user=request.user, action=InvoiceComment.CONDONE, comment=comment
        )
        messages.success(request, f"Invoice {invoice.code} condoned.")
    elif action == "delete_payment":
        old_code = invoice.code
        invoice.status = Invoice.EMITTED
        invoice.paid_at = None
        invoice.regenerate_code(save=False)
        invoice.save(update_fields=["status", "paid_at", "code", "updated_at"])
        InvoiceComment.objects.create(
            invoice=invoice,
            user=request.user,
            action=InvoiceComment.DELETE_PAYMENT,
            comment=comment,
        )
        messages.success(
            request, f"Payment deleted for {old_code}; reverted to emitted as {invoice.code}."
        )
    else:
        messages.error(request, "Unknown action.")
    return redirect("moontax:staff")


@staff_access_required
def staff_mark_pop_dead(request, extraction_id):
    """Force-finalize a not-yet-finalized pop (POST only).

    Because the laser/auto fracture notification may never arrive, staff can mark a
    pop dead at any time.  If ``fracture_time`` is missing it is set to now (type AUTO)
    so the pop has a fracture timestamp, then ``tax.finalize_pop`` is called directly
    (which does NOT re-check the despawn window — that is the intended bypass path).
    """
    if request.method != "POST":
        return redirect("moontax:staff")
    extraction = get_object_or_404(Extraction, pk=extraction_id)
    if extraction.finalized:
        messages.warning(request, f"Pop #{extraction_id} is already finalized.")
        return redirect("moontax:staff")
    if extraction.fracture_time is None:
        extraction.fracture_time = timezone.now()
        extraction.fracture_type = Extraction.AUTO
        extraction.save(update_fields=["fracture_time", "fracture_type", "updated_at"])
    tax.finalize_pop(extraction)
    messages.success(
        request,
        f"Pop #{extraction_id} ({extraction.structure}) marked dead and finalized.",
    )
    return redirect("moontax:staff")


# --------------------------------------------------------------------------------------
# Admin tab
# --------------------------------------------------------------------------------------


@admin_access_required
def admin_config(request):
    config = Configuration.get_solo()
    mining_token = TokenConfig.get_for_role(TokenConfig.MINING)
    payment_token = TokenConfig.get_for_role(TokenConfig.PAYMENT)
    if request.method == "POST" and request.POST.get("form") == "config":
        form = ConfigurationForm(request.POST, instance=config)
        if form.is_valid():
            form.save()
            messages.success(request, "Configuration saved.")
            return redirect("moontax:admin")
    else:
        form = ConfigurationForm(instance=config)

    if request.method == "POST" and request.POST.get("form") == "ore_rate":
        rate_form = OreTaxRateForm(request.POST)
        if rate_form.is_valid():
            OreTaxRate.objects.update_or_create(
                ore_type_id=rate_form.cleaned_data["ore_type_id"],
                defaults={
                    "ore_type_name": rate_form.cleaned_data["ore_type_name"],
                    "rate": rate_form.cleaned_data["rate"],
                },
            )
            messages.success(request, "Ore tax rate saved.")
            return redirect("moontax:admin")
    else:
        rate_form = OreTaxRateForm()

    context = {
        "can_staff": True,
        "can_admin": True,
        "config": config,
        "config_form": form,
        "rate_form": rate_form,
        "ore_rates": OreTaxRate.objects.all(),
        "ore_catalog_empty": not OreType.objects.exists(),
        "mining_token": mining_token,
        "payment_token": payment_token,
        "table_page_size": config.table_page_size,
    }
    return render(request, "moontax/admin.html", context)


@admin_access_required
def ore_rate_delete(request, rate_id):
    if request.method == "POST":
        OreTaxRate.objects.filter(pk=rate_id).delete()
        messages.success(request, "Ore tax rate removed.")
    return redirect("moontax:admin")


@admin_access_required
@token_required(scopes=providers.MINING_SCOPES)
def token_setup_mining(request, token):
    """SSO callback: validate a Director/CEO token for the mining corp, then store it.

    The mining corporation is taken from the token's own corp — it is not entered by
    hand — so we validate Director/CEO for whatever corp the token character is in and
    adopt that corp as the plugin's mining corporation.
    """
    config = Configuration.get_solo()
    result = providers.validate_token(token, expected_corporation_id=None, role="mining")
    if not result.ok:
        messages.error(request, f"Mining token rejected: {result.reason}")
        return redirect("moontax:admin")

    TokenConfig.objects.update_or_create(
        role=TokenConfig.MINING,
        defaults={
            "token": token,
            "character_id": token.character_id,
            "character_name": result.character_name or token.character_name,
            "corporation_id": result.corporation_id,
            "corporation_name": result.corporation_name,
            "added_by": request.user,
            "is_valid": True,
            "last_validated": timezone.now(),
            "last_error": "",
        },
    )
    # Adopt the token's corp as the mining corporation (derived, never hand-entered).
    if result.corporation_id:
        config.mining_corporation_id = result.corporation_id
        config.mining_corporation_name = result.corporation_name
        config.save(update_fields=["mining_corporation_id", "mining_corporation_name", "updated_at"])

    messages.success(
        request,
        f"Mining corp token saved for {result.character_name} ({result.corporation_name}).",
    )
    # Kick off the first-setup backfill.
    from moontax.tasks import backfill

    backfill.delay()
    return redirect("moontax:admin")


@admin_access_required
@token_required(scopes=providers.PAYMENT_SCOPES)
def token_setup_payment(request, token):
    """SSO callback: validate a Director/CEO token for the payment corp, then store it.

    The payment corporation is taken from the token's own corp — it is not entered by
    hand — so we validate Director/CEO for whatever corp the token character is in and
    adopt that corp as the plugin's payment corporation.
    """
    config = Configuration.get_solo()
    result = providers.validate_token(token, expected_corporation_id=None, role="payment")
    if not result.ok:
        messages.error(request, f"Payment token rejected: {result.reason}")
        return redirect("moontax:admin")

    TokenConfig.objects.update_or_create(
        role=TokenConfig.PAYMENT,
        defaults={
            "token": token,
            "character_id": token.character_id,
            "character_name": result.character_name or token.character_name,
            "corporation_id": result.corporation_id,
            "corporation_name": result.corporation_name,
            "added_by": request.user,
            "is_valid": True,
            "last_validated": timezone.now(),
            "last_error": "",
        },
    )
    # Adopt the token's corp as the payment corporation (derived, never hand-entered).
    if result.corporation_id:
        config.payment_corporation_id = result.corporation_id
        config.payment_corporation_name = result.corporation_name
        config.save(update_fields=["payment_corporation_id", "payment_corporation_name", "updated_at"])

    messages.success(
        request,
        f"Payment corp token saved for {result.character_name} ({result.corporation_name}).",
    )
    # Kick off the first-setup backfill.
    from moontax.tasks import backfill

    backfill.delay()
    return redirect("moontax:admin")


@admin_access_required
def token_remove(request, role):
    """Remove the ``TokenConfig`` for the given ``role`` path kwarg."""
    if request.method == "POST":
        tc = TokenConfig.get_for_role(role)
        if tc:
            tc.delete()
        messages.success(request, f"{role.capitalize()} corp token removed.")
    return redirect("moontax:admin")
