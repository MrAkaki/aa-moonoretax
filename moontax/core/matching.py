"""Player ↔ character resolution via Alliance Auth ``CharacterOwnership``."""

from __future__ import annotations


def user_for_character(character_id: int):
    """The AA ``User`` that owns ``character_id``, or ``None`` if unlinked."""
    from allianceauth.authentication.models import CharacterOwnership

    own = (
        CharacterOwnership.objects.filter(character__character_id=character_id)
        .select_related("user")
        .first()
    )
    return own.user if own else None


def character_ids_for_user(user) -> list[int]:
    """All EVE character ids linked to ``user`` (across alts)."""
    from allianceauth.authentication.models import CharacterOwnership

    return list(
        CharacterOwnership.objects.filter(user=user).values_list(
            "character__character_id", flat=True
        )
    )
