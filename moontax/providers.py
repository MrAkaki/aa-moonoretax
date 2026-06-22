"""ESI access, isolated here so tasks/validation are easy to mock.

Two corp tokens drive ESI calls, one per role (stored as :class:`~moontax.models.TokenConfig`
rows keyed by :attr:`~moontax.models.TokenConfig.role`):

- **Mining token** — used for structures, mining observers/ledgers, extractions, and
  character notifications.  Requires :data:`MINING_SCOPES`.
- **Payment token** — used for corp-contract reconciliation.  Requires
  :data:`PAYMENT_SCOPES`.

Each public function wraps a single ESI operation; the tasks layer orchestrates them.

**Path quirk (do not "fix"):** the mining endpoints use the **singular** ``/corporation/``
path → operation ids ``GetCorporationCorporationId…`` under the **Industry** tag.
Structures use the **plural** ``/corporations/`` →
``GetCorporationsCorporationId…`` under the **Corporation** tag.
Contracts also use the **plural** ``/corporations/`` path but live under the
**Contracts** tag → ``GetCorporationsCorporationIdContracts…``.
"""

from __future__ import annotations

import logging

from moontax import __version__

logger = logging.getLogger(__name__)

from esi.exceptions import HTTPNotModified  # noqa: E402
from esi.openapi_clients import ESIClientProvider  # noqa: E402

try:  # pin to the installed django-esi's compatibility date
    from esi import __esi_compatibility_date__ as _DEFAULT_COMPAT_DATE
except ImportError:  # pragma: no cover - other django-esi builds
    _DEFAULT_COMPAT_DATE = "2026-05-19"


def _compat_date() -> str:
    from moontax import app_settings

    return app_settings.MOONTAX_ESI_COMPATIBILITY_DATE or _DEFAULT_COMPAT_DATE


esi = ESIClientProvider(
    compatibility_date=_compat_date(),
    ua_appname="aa-moonoretax",
    ua_version=__version__,
    ua_url="https://github.com/MrAkaki/aa-moonoretax",
    # Only the tags whose operations this app calls.
    tags=["Contracts", "Corporation", "Industry", "Character", "Universe"],
)


def _g(obj, key, default=None):
    """Read ``key`` from an ESI result whether it's a dict or a pydantic model.

    The django-esi aiopenapi3 client returns pydantic model objects (e.g.
    ``CharactersDetail``), not dicts, so plain ``.get()`` raises ``AttributeError``.
    """
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _results(operation, force_refresh: bool = False):
    """Run a paginated ESI ``.results()`` call, tolerating a stale-ETag 304.

    Like :func:`_result`, this works around django-esi caching the ETag separately
    from the response body: when the body cache has expired but the ETag persists,
    ESI answers **304** and django-esi raises :class:`HTTPNotModified` with no payload.
    Returning ``[]`` here would be wrong whenever our DB has not yet been populated
    (e.g. first-setup backfill), so we retry with ``force_refresh`` to fetch the body.
    """
    try:
        return operation.results(force_refresh=force_refresh)
    except HTTPNotModified:  # pragma: no cover - exercised against live ESI
        if force_refresh:
            return []
        return operation.results(force_refresh=True)


def _result(operation):
    """Run a single-object ESI ``.result()``, tolerating a stale-ETag 304.

    django-esi caches the ETag separately from the response body; when the body cache
    has expired but the ETag is still stored it sends ``If-None-Match``, ESI answers
    **304** and django-esi raises :class:`HTTPNotModified` with no payload. Retrying with
    ``force_refresh`` drops the ETag and fetches the full body. (Paginated calls go
    through :func:`_results`, which can safely treat 304 as "nothing changed"; a single
    lookup cannot — the caller needs the object.)
    """
    try:
        return operation.result()
    except HTTPNotModified:  # pragma: no cover - exercised against live ESI
        return operation.result(force_refresh=True)


# --------------------------------------------------------------------------------------
# Token retrieval
# --------------------------------------------------------------------------------------

# Scopes required for the mining-corp Director/CEO token (structures, ledger,
# extractions, notifications, universe structure info).
MINING_SCOPES = [
    "esi-industry.read_corporation_mining.v1",
    "esi-corporations.read_structures.v1",
    "esi-universe.read_structures.v1",
    "esi-characters.read_notifications.v1",
]

# Scopes required for the payment-corp Director/CEO token (contracts,
# universe structure info for location resolution).
PAYMENT_SCOPES = [
    "esi-contracts.read_corporation_contracts.v1",
    "esi-universe.read_structures.v1",
]


def get_mining_token():
    """Mining-corp token validated for :data:`MINING_SCOPES`; ``None`` if unusable."""
    from moontax.models import TokenConfig

    cfg = TokenConfig.get_for_role(TokenConfig.MINING)
    if not cfg or not cfg.token_id:
        return None
    from esi.models import Token

    return (
        Token.objects.filter(pk=cfg.token_id)
        .require_scopes(MINING_SCOPES)
        .require_valid()
        .first()
    )


def get_payment_token():
    """Payment-corp token validated for :data:`PAYMENT_SCOPES`; ``None`` if unusable."""
    from moontax.models import TokenConfig

    cfg = TokenConfig.get_for_role(TokenConfig.PAYMENT)
    if not cfg or not cfg.token_id:
        return None
    from esi.models import Token

    return (
        Token.objects.filter(pk=cfg.token_id)
        .require_scopes(PAYMENT_SCOPES)
        .require_valid()
        .first()
    )


# --------------------------------------------------------------------------------------
# Authenticated corp endpoints
# --------------------------------------------------------------------------------------


def corp_structures(token, corporation_id: int) -> list[dict]:
    """``/corporations/{id}/structures/`` — fuel_expires + services[] (Moon Drilling)."""
    return _results(
        esi.client.Corporation.GetCorporationsCorporationIdStructures(
            corporation_id=corporation_id, token=token
        )
    )


def mining_observers(token, corporation_id: int) -> list[dict]:
    """``/corporation/{id}/mining/observers`` (singular) — observer ids + last_updated."""
    return _results(
        esi.client.Industry.GetCorporationCorporationIdMiningObservers(
            corporation_id=corporation_id, token=token
        )
    )


def mining_observer_ledger(token, corporation_id: int, observer_id: int) -> list[dict]:
    """``/corporation/{id}/mining/observers/{observer_id}`` (singular) — ledger rows."""
    return _results(
        esi.client.Industry.GetCorporationCorporationIdMiningObserversObserverId(
            corporation_id=corporation_id, observer_id=observer_id, token=token
        )
    )


def mining_extractions(token, corporation_id: int) -> list[dict]:
    """``/corporation/{id}/mining/extractions`` (singular) — scheduled extractions."""
    return _results(
        esi.client.Industry.GetCorporationCorporationIdMiningExtractions(
            corporation_id=corporation_id, token=token
        )
    )


def corp_contracts(token, corporation_id: int) -> list[dict]:
    """``/corporations/{id}/contracts/`` — all corp contracts."""
    return _results(
        esi.client.Contracts.GetCorporationsCorporationIdContracts(
            corporation_id=corporation_id, token=token
        )
    )


def contract_items(token, corporation_id: int, contract_id: int) -> list[dict]:
    """``/corporations/{id}/contracts/{contract_id}/items/`` — offered/requested items."""
    return _results(
        esi.client.Contracts.GetCorporationsCorporationIdContractsContractIdItems(
            corporation_id=corporation_id, contract_id=contract_id, token=token
        )
    )


def character_notifications(token) -> list[dict]:
    """``/characters/{id}/notifications/`` — corp moon notifications land in this feed."""
    return _results(
        esi.client.Character.GetCharactersCharacterIdNotifications(
            character_id=token.character_id, token=token
        )
    )


def structure_info(token, structure_id: int) -> dict:
    """``/universe/structures/{id}/`` — resolve a structure's name/system/type."""
    return _result(
        esi.client.Universe.GetUniverseStructuresStructureId(
            structure_id=structure_id, token=token
        )
    )


# --------------------------------------------------------------------------------------
# Public endpoints (no token)
# --------------------------------------------------------------------------------------


def corporation_info(corporation_id: int) -> dict:
    """``/corporations/{id}/`` — public: name, ticker, ceo_id."""
    return _result(
        esi.client.Corporation.GetCorporationsCorporationId(
            corporation_id=corporation_id
        )
    )


def character_info(character_id: int) -> dict:
    """``/characters/{id}/`` — public: name, corporation_id."""
    return _result(
        esi.client.Character.GetCharactersCharacterId(character_id=character_id)
    )


def universe_group(group_id: int) -> dict:
    """``/universe/groups/{id}/`` — public: group name + member ``types`` ids.

    Used to enumerate moon ores (the five moon-asteroid groups) for the per-ore tax
    dropdown. No token required.
    """
    return _result(esi.client.Universe.GetUniverseGroupsGroupId(group_id=group_id))


def universe_moon(moon_id: int) -> dict:
    """``/universe/moons/{moon_id}/`` — public: moon name + system_id.

    Used to resolve moon names and their parent system IDs.  The bulk
    ``POST /universe/names/`` endpoint does NOT support moon IDs (returns HTTP 404),
    so each moon must be looked up individually via this endpoint. No token required.
    """
    return _result(
        esi.client.Universe.GetUniverseMoonsMoonId(moon_id=moon_id)
    )


def resolve_names(ids) -> dict[int, dict]:
    """``POST /universe/names`` — bulk id→{name, category}. Returns ``{id: row}``."""
    ids = [int(i) for i in dict.fromkeys(ids)]  # de-dup, keep order
    if not ids:
        return {}
    rows = _result(esi.client.Universe.PostUniverseNames(body=ids))
    out: dict[int, dict] = {}
    for row in rows:
        out[int(_g(row, "id"))] = {
            "id": _g(row, "id"),
            "name": _g(row, "name", ""),
            "category": _g(row, "category", ""),
        }
    return out


# --------------------------------------------------------------------------------------
# Token validation (Director / CEO in the target corp)
# --------------------------------------------------------------------------------------


class ValidationResult:
    """Outcome of :func:`validate_token`."""

    def __init__(self, ok, *, corporation_id=None, corporation_name="",
                 character_name="", is_ceo=False, reason=""):
        self.ok = ok
        self.corporation_id = corporation_id
        self.corporation_name = corporation_name
        self.character_name = character_name
        self.is_ceo = is_ceo
        self.reason = reason

    def __bool__(self) -> bool:
        return self.ok


def _is_forbidden(exc) -> bool:
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status == 403:
        return True
    return "403" in str(exc) or "forbidden" in str(exc).lower()


def validate_token(
    token,
    expected_corporation_id: int | None = None,
    *,
    role: str = "mining",
) -> ValidationResult:
    """Confirm the token character holds Director/CEO in the expected corp.

    The required scopes do not include corp roles, so we validate **operationally**
    (Requirements §2/§7): the character's corp must match ``expected_corporation_id``
    (when supplied), and a role-appropriate ESI call must succeed (a 403 means the
    in-game Director/Station-Manager role was lost). CEO is additionally confirmed via
    the corp's ``ceo_id``.

    Role-specific decisive check:
    - ``"mining"`` → :func:`corp_structures` (requires structures scope + Director role).
    - ``"payment"`` → :func:`corp_contracts` (requires contracts scope + Director role;
      the payment token need not carry the structures scope).
    """
    try:
        char = character_info(token.character_id)
    except Exception as exc:  # noqa: BLE001 - any ESI failure ⇒ invalid
        logger.warning("moontax token validation: character lookup failed: %s", exc)
        return ValidationResult(False, reason=f"Character lookup failed: {exc}")

    corp_id = _g(char, "corporation_id")
    char_name = _g(char, "name", "")

    if expected_corporation_id and corp_id != expected_corporation_id:
        return ValidationResult(
            False,
            corporation_id=corp_id,
            character_name=char_name,
            reason=(
                f"Token character is in corp {corp_id}, "
                f"not the expected corp {expected_corporation_id}."
            ),
        )

    corp_name = ""
    is_ceo = False
    try:
        corp = corporation_info(corp_id)
        corp_name = _g(corp, "name", "")
        is_ceo = _g(corp, "ceo_id") == token.character_id
    except Exception as exc:  # noqa: BLE001 - non-fatal; name/ceo are informational
        logger.info("moontax token validation: corp lookup failed: %s", exc)

    # The decisive check: can this token perform the role-appropriate ESI call?
    # Mining tokens are verified via corp-structures (403 ⇒ no Director role).
    # Payment tokens are verified via corp-contracts (structures scope not required).
    try:
        if role == "payment":
            corp_contracts(token, corp_id)
        else:
            corp_structures(token, corp_id)
    except Exception as exc:  # noqa: BLE001
        if _is_forbidden(exc):
            endpoint = "corp-contracts" if role == "payment" else "corp-structures"
            return ValidationResult(
                False,
                corporation_id=corp_id,
                corporation_name=corp_name,
                character_name=char_name,
                is_ceo=is_ceo,
                reason=f"No {endpoint} access (Director/CEO role lost or never held).",
            )
        check_name = "Contracts check" if role == "payment" else "Structures check"
        return ValidationResult(
            False,
            corporation_id=corp_id,
            corporation_name=corp_name,
            character_name=char_name,
            is_ceo=is_ceo,
            reason=f"{check_name} failed: {exc}",
        )

    return ValidationResult(
        True,
        corporation_id=corp_id,
        corporation_name=corp_name,
        character_name=char_name,
        is_ceo=is_ceo,
    )
