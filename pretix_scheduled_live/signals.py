import json
import logging

from django.dispatch import receiver
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _, pgettext_lazy
from pretix.base.signals import (
    api_event_settings_fields,
    logentry_display,
    periodic_task,
    timeline_events,
)
from pretix.control.signals import event_dashboard_top, html_head, nav_event_settings

from .services import (
    blocking_reasons,
    get_scheduled_datetime,
    plugin_is_active,
    run_due_transitions_guarded,
)
from .transitions import GO_LIVE, TRANSITIONS

logger = logging.getLogger(__name__)

LIVE_PAGE_URL_NAME = "event.live"


# ---------------------------------------------------------------------------
# Periodic task
# ---------------------------------------------------------------------------

@receiver(periodic_task, dispatch_uid="pretix_scheduled_live_periodic_task")
def on_periodic_task(sender, **kwargs):
    """
    Entry point for pretix' periodic cron.

    Its dotted path, ``pretix_scheduled_live.signals.on_periodic_task``, is part
    of this plugin's public interface: it is what you pass to
    ``runperiodic --tasks`` to schedule only this check every minute. Renaming
    this function breaks documented cron lines.
    """
    return run_due_transitions_guarded()


# ---------------------------------------------------------------------------
# Settings navigation
# ---------------------------------------------------------------------------

@receiver(nav_event_settings, dispatch_uid="pretix_scheduled_live_nav_settings")
def on_nav_event_settings(sender, request=None, **kwargs):
    if not request.user.has_event_permission(
        request.organizer, request.event, "can_change_event_settings", request=request
    ):
        return []
    from .views import settings_url

    url = settings_url(request.event)
    return [{
        "label": _("Scheduled publication"),
        "url": url,
        "active": request.path.rstrip("/") == url.rstrip("/"),
    }]


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

@receiver(timeline_events, dispatch_uid="pretix_scheduled_live_timeline")
def on_timeline_events(sender, subevent=None, **kwargs):
    """
    Show the scheduled publication in the event dashboard timeline, next to the
    presale start and the event date. The pencil links to our settings tab, the
    same way core links ``presale_start`` to the general settings page.
    """
    from pretix.base.timeline import TimelineEvent

    from .views import settings_url

    scheduled = get_scheduled_datetime(sender, GO_LIVE)
    if not scheduled:
        return []

    return [TimelineEvent(
        event=sender,
        subevent=subevent,
        datetime=scheduled,
        description=pgettext_lazy("timeline", "The ticket shop goes live"),
        edit_url=settings_url(sender),
        edit_permission="can_change_event_settings",
    )]


# ---------------------------------------------------------------------------
# Dashboard widget
# ---------------------------------------------------------------------------

@receiver(event_dashboard_top, dispatch_uid="pretix_scheduled_live_dashboard_top")
def on_event_dashboard_top(sender, request=None, **kwargs):
    """
    ``event_dashboard_top`` is used rather than ``event_dashboard_widgets``
    because the latter is sent without the request, which we need both for the
    permission check and for the CSRF token of the cancel button.
    """
    return _render_panel(sender, request)


# ---------------------------------------------------------------------------
# "Shop status" page
# ---------------------------------------------------------------------------

@receiver(html_head, dispatch_uid="pretix_scheduled_live_html_head")
def on_html_head(sender, request=None, **kwargs):
    """
    pretix offers no signal to add content to the "Shop status" page, so we
    inject our panel from the document head once the DOM is ready.
    """
    if not request or not getattr(request, "resolver_match", None):
        return ""
    if request.resolver_match.url_name != LIVE_PAGE_URL_NAME:
        return ""

    html = _render_panel(sender, request)
    if not html:
        return ""

    return mark_safe(
        '<script type="text/javascript">document.addEventListener('
        '"DOMContentLoaded", function () {'
        "var html = " + json.dumps(html) + ";"
        'var anchor = document.querySelector(".content-wrapper .panel, #page-wrapper .panel, .panel");'
        "if (!anchor || !anchor.parentNode) { return; }"
        'var box = document.createElement("div");'
        "box.innerHTML = html;"
        "anchor.parentNode.insertBefore(box.firstElementChild || box, anchor);"
        "});</script>"
    )


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------

@receiver(api_event_settings_fields, dispatch_uid="pretix_scheduled_live_api_settings_fields")
def on_api_event_settings_fields(sender, **kwargs):
    from rest_framework import serializers

    return {
        transition.settings_key: serializers.DateTimeField(
            required=False,
            allow_null=True,
            help_text=str(transition.label),
        )
        for transition in TRANSITIONS
    }


# ---------------------------------------------------------------------------
# Log entry display
# ---------------------------------------------------------------------------

LOG_ENTRY_TYPES = {
    "pretix_scheduled_live.scheduled": _("A scheduled publication has been set for the shop."),
    "pretix_scheduled_live.canceled": _("The scheduled publication of the shop has been cancelled."),
    "pretix_scheduled_live.activation_failed": _(
        "The shop could not be published at the scheduled time."
    ),
}


@receiver(logentry_display, dispatch_uid="pretix_scheduled_live_logentry_display")
def on_logentry_display(sender, logentry, **kwargs):
    return LOG_ENTRY_TYPES.get(logentry.action_type)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _render_panel(event, request):
    """Render the info panel, or return ``""`` when there is nothing to show."""
    ctx = _panel_context(event, request)
    if not ctx:
        return ""
    # Deliberately rendered *without* ``request=``: this code runs from a
    # template context processor, and passing the request would re-run every
    # context processor — including the one that emits ``html_head``.
    # ``{% csrf_token %}`` only needs ``csrf_token`` to be in the context.
    return render_to_string("pretix_scheduled_live/panel.html", ctx)


def _panel_context(event, request):
    """Context for the info panel, or ``None`` when there is nothing to show."""
    if not request or not plugin_is_active(event):
        return None
    if not request.user.has_event_permission(
        request.organizer, event, "can_change_event_settings", request=request
    ):
        return None

    scheduled = get_scheduled_datetime(event, GO_LIVE)
    if not scheduled:
        return None

    from .views import cancel_url, settings_url

    from django.middleware.csrf import get_token

    return {
        "csrf_token": get_token(request),
        "event": event,
        "scheduled_for": scheduled.astimezone(event.timezone),
        "issues": blocking_reasons(event, GO_LIVE),
        "already_live": event.live,
        "cancel_url": cancel_url(event),
        "settings_url": settings_url(event),
        "next_url": request.path,
    }
