# aa-moonoretax

Per-player **moon-mining ore tax** and **ore-paid invoicing** plugin for
[Alliance Auth](https://allianceauth.org/) 5+.

- Distribution name: `aa-moonoretax`
- Import / Django app label: `moontax`

## What it does

Monitors moon mining for a **single corporation** via EVE ESI, taxes each player on the
ore they personally mined per moon pop, and bills each player an **invoice paid in ore**
through an in-game `item_exchange` contract. Invoices and reminders are delivered over
Discord (via `allianceauth-discordbot`, falling back to Alliance Auth's built-in
notifications).

## Permissions

Hierarchical (`admin` ⊃ `staff` ⊃ `basic_access`):

| Permission | Grants |
| --- | --- |
| `moontax.basic_access` | User Dashboard |
| `moontax.staff_access` | Staff Dashboard (+ User) |
| `moontax.admin_access` | Admin tab — token setup & configuration (+ Staff + User) |

## Installation (overview)

1. `pip install git+https://github.com/MrAkaki/aa-moonoretax.git@main`
2. Add `"moontax"` to `INSTALLED_APPS`.
3. Register the required ESI scopes.
4. Add the Celery beat schedule: `moontax.tasks.run_hourly` (hourly collection) and
   `moontax.tasks.update_ore_catalog` (weekly — refreshes the moon-ore tax list).
5. `python manage.py migrate`
6. `python manage.py moontax_load_ores` — populate the moon-ore catalog from public ESI.
   No corp token needed; the weekly beat keeps it current. This loads the base ores shown
   in the per-ore tax dropdown **plus** their quality/compressed variants (e.g. "Glistening
   Bitumens"), each mapped to its base ore — so a rate set on a base ore automatically
   applies to all of its variants.
7. In the **Admin** tab, set up the corp token (added by a Director or CEO) and configure
   tax rates / windows.


## Development

This repo carries a standalone unit-test suite (all ESI/Discord calls mocked) and an
integration path against the Dockerized Alliance Auth stack.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
