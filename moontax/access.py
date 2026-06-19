"""Hierarchical permission helpers and view decorators.

Django permissions are flat, but moontax wants three nested levels:
``admin_access`` ⊃ ``staff_access`` ⊃ ``basic_access``. A user holding a higher level
is treated as holding every lower one, regardless of which codenames are actually
assigned. All access checks go through this module so the hierarchy is defined once.
"""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

BASIC = "moontax.basic_access"
STAFF = "moontax.staff_access"
ADMIN = "moontax.admin_access"


def can_admin(user) -> bool:
    return user.has_perm(ADMIN)


def can_staff(user) -> bool:
    """Staff Dashboard: explicit staff, or admin (which implies staff)."""
    return user.has_perm(STAFF) or user.has_perm(ADMIN)


def can_basic(user) -> bool:
    """User Dashboard: any of the three levels."""
    return user.has_perm(BASIC) or user.has_perm(STAFF) or user.has_perm(ADMIN)


def access_level(user) -> str | None:
    """Highest level the user holds: ``"admin"`` / ``"staff"`` / ``"basic"`` / ``None``."""
    if can_admin(user):
        return "admin"
    if can_staff(user):
        return "staff"
    if can_basic(user):
        return "basic"
    return None


def _gate(check):
    """Build a ``login_required`` + hierarchical-permission view decorator."""

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not check(request.user):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return login_required(wrapper)

    return decorator


basic_access_required = _gate(can_basic)
staff_access_required = _gate(can_staff)
admin_access_required = _gate(can_admin)
