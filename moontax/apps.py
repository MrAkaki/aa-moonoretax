from django.apps import AppConfig

from moontax import __version__


class MoontaxConfig(AppConfig):
    name = "moontax"
    label = "moontax"
    verbose_name = f"Moon Ore Tax v{__version__}"
