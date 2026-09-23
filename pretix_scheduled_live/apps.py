from django.utils.translation import gettext_lazy as _
from pretix.base.plugins import PluginConfig

from . import __version__


class ScheduledLivePluginConfig(PluginConfig):
    name = "pretix_scheduled_live"
    verbose_name = "Scheduled Live"

    class Meta:
        app_label = "pretix_scheduled_live"

    class PretixPluginMeta:
        name = _("Scheduled shop publication")
        author = "Valentin Gosselin"
        category = "FEATURE"
        description = _(
            "Schedule the exact date and time at which a ticket shop goes live, so it can be "
            "synchronised with scheduled publications on WordPress or social networks."
        )
        visible = True
        version = __version__
        compatibility = "pretix>=2024.1.0"

    def ready(self):
        from . import signals  # noqa

    @property
    def url_namespace(self):
        return "pretix_scheduled_live"

    @property
    def urls(self):
        from .urls import urlpatterns

        return urlpatterns


default_app_config = "pretix_scheduled_live.apps.ScheduledLivePluginConfig"
