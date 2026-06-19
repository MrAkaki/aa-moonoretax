"""Custom managers — the home for the trap-prone write semantics.

Most important: :class:`MiningLedgerManager.upsert_row` **overwrites** quantity (the ESI
value is cumulative-to-date for the key), so polling twice never double-counts.
"""

from __future__ import annotations

import secrets

from django.db import models


class MiningLedgerManager(models.Manager):
    def upsert_row(
        self,
        *,
        observer_id: int,
        character_id: int,
        ore_type_id: int,
        recorded_date,
        quantity: int,
        structure=None,
        recorded_corporation_id: int | None = None,
        last_updated=None,
    ):
        """Insert or **overwrite** a ledger cell keyed on
        ``(observer_id, character_id, ore_type_id, recorded_date)``.

        ``quantity`` is cumulative-to-date for the key, so we *set* it (never add).
        Returns ``(obj, created)``.
        """
        defaults = {
            "quantity": quantity,
            "recorded_corporation_id": recorded_corporation_id,
            "last_updated": last_updated,
        }
        if structure is not None:
            defaults["structure"] = structure
        return self.update_or_create(
            observer_id=observer_id,
            character_id=character_id,
            ore_type_id=ore_type_id,
            recorded_date=recorded_date,
            defaults=defaults,
        )


class UnmatchedMinerManager(models.Manager):
    def upsert_row(
        self,
        *,
        observer_id: int,
        character_id: int,
        ore_type_id: int,
        recorded_date,
        quantity: int,
        character_name: str = "",
        structure=None,
    ):
        """Same overwrite semantics as the ledger, for unlinked characters."""
        defaults = {"quantity": quantity}
        if character_name:
            defaults["character_name"] = character_name
        if structure is not None:
            defaults["structure"] = structure
        return self.update_or_create(
            observer_id=observer_id,
            character_id=character_id,
            ore_type_id=ore_type_id,
            recorded_date=recorded_date,
            defaults=defaults,
        )


class OreTaxRateManager(models.Manager):
    def rate_for(self, ore_type_id: int):
        """Explicit per-ore rate, or ``None`` if the caller should use the config default."""
        row = self.filter(ore_type_id=ore_type_id).first()
        return row.rate if row else None


class OreTypeManager(models.Manager):
    def replace_catalog(self, entries: dict[int, dict]) -> int:
        """Upsert ``{type_id: {"name", "group_id", "base_type_id"}}`` and drop rows no longer listed.

        Mirrors the catalog to ESI exactly, so an ore CCP removes disappears from the
        dropdown on the next sync. Variants carry a non-null ``base_type_id`` pointing at
        their base ore; base ores have ``base_type_id=None``. Returns the resulting
        catalog size.
        """
        for type_id, fields in entries.items():
            self.update_or_create(
                type_id=type_id,
                defaults={
                    "name": fields.get("name", ""),
                    "group_id": fields.get("group_id"),
                    "base_type_id": fields.get("base_type_id"),
                },
            )
        self.exclude(type_id__in=list(entries)).delete()
        return self.count()

    def choices(self) -> list[tuple[int, str]]:
        """``(type_id, name)`` pairs (rarity-ordered) for the per-ore tax dropdown.

        Only base ores (``base_type_id`` is null) are shown — variants inherit their
        base ore's rate and do not need a separate dropdown entry.
        """
        return [
            (t, n)
            for t, n in self.filter(base_type_id__isnull=True).values_list("type_id", "name")
        ]

    def compressed_type_id(self, raw_type_id: int) -> int | None:
        """The compressed counterpart of a raw mined ore (same quality tier), or None.

        Mined ore is always raw/quality (e.g. "Brimful Bitumens"); its compressed form
        carries the same name with a "Compressed " prefix ("Compressed Brimful Bitumens").
        Returns None for an unknown ore or one that is itself already compressed.
        """
        row = self.filter(type_id=raw_type_id).first()
        if row is None or not row.name or row.name.startswith("Compressed "):
            return None
        comp = self.filter(name=f"Compressed {row.name}").first()
        return comp.type_id if comp else None

    def effective_type_id(self, type_id: int) -> int:
        """Return the type_id to use for rate lookup.

        If ``type_id`` is a quality/compressed variant with a known base, returns the
        base ore's type_id so the variant inherits the base ore's explicit rate.
        Falls back to the given ``type_id`` when the row is absent or is already a
        base ore (``base_type_id`` is null).
        """
        row = self.filter(type_id=type_id).first()
        return row.base_type_id if (row and row.base_type_id) else type_id


class EveNameManager(models.Manager):
    def get_name(self, eve_id: int) -> str:
        row = self.filter(eve_id=eve_id).first()
        return row.name if row and row.name else str(eve_id)

    def set_name(self, eve_id: int, name: str, category: str = "") -> None:
        self.update_or_create(
            eve_id=eve_id, defaults={"name": name, "category": category}
        )

    def name_map(self, eve_ids) -> dict[int, str]:
        return {r.eve_id: r.name for r in self.filter(eve_id__in=list(eve_ids))}


class InvoiceManager(models.Manager):
    CODE_PREFIX = "MT"

    def generate_code(self) -> str:
        """A short, unique, human-typable invoice code (goes in the contract title)."""
        while True:
            code = f"{self.CODE_PREFIX}-{secrets.token_hex(3).upper()}"
            if not self.filter(code=code).exists():
                return code
