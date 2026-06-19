"""Alliance Auth hook registration: a permission-gated menu item plus the URL mount."""

from allianceauth import hooks
from allianceauth.services.hooks import MenuItemHook, UrlHook
from django.utils.translation import gettext_lazy as _

from moontax import urls
from moontax.access import can_basic


class MoontaxMenuItem(MenuItemHook):
    def __init__(self):
        super().__init__(
            text=_("Moon Ore Tax"),
            classes="fas fa-gem fa-fw",
            url_name="moontax:index",
            navactive=["moontax:"],
        )

    def render(self, request):
        # Any access level lands on the dashboard; the view itself routes tabs by level.
        if can_basic(request.user):
            return super().render(request)
        return ""


@hooks.register("menu_item_hook")
def register_menu():
    return MoontaxMenuItem()


@hooks.register("url_hook")
def register_urls():
    return UrlHook(urls, "moontax", r"^moontax/")
