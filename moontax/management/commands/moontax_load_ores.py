"""Populate the moon-ore catalog (the per-ore tax dropdown) from public ESI.

Run once as a setup step after ``migrate`` so the Admin tab's per-ore dropdown is
usable immediately. Needs no corp token (public endpoints). The weekly
``update_ore_catalog`` beat task keeps it current afterwards.
"""

from django.core.management.base import BaseCommand

from moontax import tasks


class Command(BaseCommand):
    help = "Load the moon-ore catalog (per-ore tax dropdown) from public ESI."

    def handle(self, *args, **options):
        self.stdout.write("Loading moon-ore catalog from ESI…")
        count = tasks.load_ore_catalog()
        self.stdout.write(self.style.SUCCESS(f"Done — {count} moon ores in catalog."))
