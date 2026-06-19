"""Builders for the Django/ORM tests (run in-container via ``manage.py test moontax``)."""

import datetime as dt

from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter
from django.contrib.auth.models import User

from moontax.models import Configuration, Extraction, MiningLedger, Structure

UTC = dt.timezone.utc


def make_config(**overrides) -> Configuration:
    cfg = Configuration.get_solo()
    for k, v in overrides.items():
        setattr(cfg, k, v)
    cfg.save()
    return cfg


def make_user(username: str) -> User:
    return User.objects.create(username=username)


def link_character(user: User, character_id: int, name: str, owner_hash: str | None = None):
    char = EveCharacter.objects.create(
        character_id=character_id,
        character_name=name,
        corporation_id=2001,
        corporation_name="Corp",
        corporation_ticker="CORP",
    )
    CharacterOwnership.objects.create(
        character=char, owner_hash=owner_hash or f"h{character_id}", user=user
    )
    return char


def make_structure(structure_id: int = 1001, name: str = "Drill") -> Structure:
    return Structure.objects.create(
        structure_id=structure_id,
        name=name,
        corporation_id=2001,
        has_moon_drilling=True,
        drill_state="online",
    )


def make_extraction(structure: Structure, chunk_arrival: dt.datetime, **kw) -> Extraction:
    return Extraction.objects.create(
        structure=structure, chunk_arrival_time=chunk_arrival, **kw
    )


def make_ledger(observer_id, character_id, ore_type_id, recorded_date, quantity, structure=None):
    return MiningLedger.objects.create(
        observer_id=observer_id,
        character_id=character_id,
        ore_type_id=ore_type_id,
        recorded_date=recorded_date,
        quantity=quantity,
        structure=structure,
    )
