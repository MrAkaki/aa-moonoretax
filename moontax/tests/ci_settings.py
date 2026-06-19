"""Settings shim for in-container verification (migrations / check / test).

Builds on the testsite's ``myauth.settings.local`` (the proven full AA config) but swaps
the database to SQLite so migrations, ``check`` and the test suite can run in a one-off
container without the MariaDB/redis stack. Not used in production.
"""

from myauth.settings.local import *  # noqa: F401,F403

DATABASES["default"] = {  # noqa: F405
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": ":memory:",
}

# The Discord service app eagerly builds a redis-backed bot client at import, which a
# one-off (no redis) can't satisfy. moontax only imports aadiscordbot lazily at runtime,
# so drop the Discord apps here — migrations/check/tests don't need them.
_DISCORD_APPS = {"allianceauth.services.modules.discord", "aadiscordbot"}
INSTALLED_APPS = [a for a in INSTALLED_APPS if a not in _DISCORD_APPS]  # noqa: F405

if "moontax" not in INSTALLED_APPS:
    INSTALLED_APPS = INSTALLED_APPS + ["moontax"]

# Keep the inherited redis cache (AA core's task_statistics requires a real redis at
# startup); the verification one-off joins the compose redis network.
