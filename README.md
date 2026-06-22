# aa-moonoretax

Per-player **moon-mining ore tax** and **ore-paid invoicing** plugin for
[Alliance Auth](https://allianceauth.org/) 5+.

- Distribution name: `aa-moonoretax`
- Import / Django app label: `moontax`

## What it does

Monitors moon mining for a **mining corporation** via EVE ESI, taxes each player on the
ore they personally mined per moon pop, and bills each player an **invoice paid in ore**
through an in-game `item_exchange` contract issued to a separate **payment corporation**.
Invoices and reminders are delivered over Discord (via `allianceauth-discordbot`, falling
back to Alliance Auth's built-in notifications).

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
7. In the **Admin** tab, set up **both** corp tokens — the mining-corp token and the
   payment-corp token (each added by a Director or CEO of that corp) — then configure tax
   rates / windows. Both tokens are required; if either is missing or broken, all ESI
   collection halts and plugin admins are notified.


## Updating

To upgrade an existing install:

1. Activate your Alliance Auth virtualenv.
2. Upgrade the package (pin a tag/commit instead of `@main` for reproducible deploys):
   ```
   pip install --upgrade git+https://github.com/MrAkaki/aa-moonoretax.git@main
   ```
3. Apply any new database migrations:
   ```
   python manage.py migrate moontax
   ```
4. Register any newly required ESI scopes (a release may add scopes — see the Admin tab),
   then refresh static assets if the front end changed:
   ```
   python manage.py collectstatic --noinput
   ```
5. Restart your Alliance Auth services so the new code **and** the Celery beat schedule
   reload — gunicorn, the Celery worker, and Celery **beat**. For a supervisor-based
   install:
   ```
   supervisorctl restart myauth:
   ```
   On a Dockerized AA stack, rebuild the image and recreate the containers instead.
6. Optional: `python manage.py moontax_load_ores` to refresh the moon-ore catalog if a
   release adds new ores (the weekly beat normally keeps it current).

### Upgrade notes

- **Single corp → mining/payment split:** upgrading from a version that used one corp
  token relabels your existing token as the **mining-corp** token. You must then add the
  **payment-corp** token from the Admin tab. Until both tokens are present and valid, all
  ESI collection is halted by design and plugin admins are notified.

## Development

This repo carries a standalone unit-test suite (all ESI/Discord calls mocked) and an
integration path against the Dockerized Alliance Auth stack.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
