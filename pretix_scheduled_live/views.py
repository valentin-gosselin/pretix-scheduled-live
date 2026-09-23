import logging

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import FormView
from pretix.control.permissions import EventPermissionRequiredMixin
from pretix.control.views.event import EventSettingsViewMixin

from .forms import ScheduledLiveSettingsForm
from .services import blocking_reasons, get_scheduled_datetime, set_scheduled_datetime
from .transitions import GO_LIVE

logger = logging.getLogger(__name__)


def settings_url(event):
    return reverse(
        "plugins:pretix_scheduled_live:settings",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )


def cancel_url(event):
    return reverse(
        "plugins:pretix_scheduled_live:cancel",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )


class ScheduledLiveSettingsView(EventPermissionRequiredMixin, EventSettingsViewMixin, FormView):
    form_class = ScheduledLiveSettingsForm
    template_name = "pretix_scheduled_live/settings.html"
    permission = "can_change_event_settings"

    def get_initial(self):
        return {
            field: get_scheduled_datetime(self.request.event, transition)
            for field, transition in ScheduledLiveSettingsForm.FIELD_TRANSITIONS.items()
        }

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        event = self.request.event
        ctx["scheduled_live_datetime"] = get_scheduled_datetime(event, GO_LIVE)
        ctx["live_issues"] = blocking_reasons(event, GO_LIVE)
        ctx["cancel_url"] = cancel_url(event)
        return ctx

    def form_valid(self, form):
        event = self.request.event
        for field, transition in ScheduledLiveSettingsForm.FIELD_TRANSITIONS.items():
            old = get_scheduled_datetime(event, transition)
            new = form.cleaned_data.get(field)
            if old == new:
                continue

            set_scheduled_datetime(event, new, transition)
            event.log_action(
                "pretix_scheduled_live.scheduled" if new else "pretix_scheduled_live.canceled",
                user=self.request.user,
                data={
                    "transition": transition.settings_key,
                    "scheduled_for": new.isoformat() if new else None,
                },
            )

            if not new:
                messages.success(self.request, _("Scheduled publication cancelled."))
                continue

            if event.live and transition.target_live:
                messages.warning(
                    self.request,
                    _("Your shop is already live, the scheduled publication will have no effect."),
                )
            elif new <= now():
                messages.warning(
                    self.request,
                    _(
                        "The date you entered is in the past. The shop will be published at the "
                        "next run of the periodic task."
                    ),
                )
            else:
                messages.success(self.request, _("Scheduled publication saved."))

            reasons = blocking_reasons(event, transition)
            if reasons:
                messages.warning(
                    self.request,
                    _(
                        "The shop cannot go live at the moment. Unless you fix the following "
                        "issues before the scheduled date, it will not be published and your "
                        "team will be notified by e-mail."
                    ),
                )

        return redirect(self.get_success_url())

    def get_success_url(self):
        return settings_url(self.request.event)


class ScheduledLiveCancelView(EventPermissionRequiredMixin, View):
    """POST-only endpoint used by the dashboard and shop-status panels."""

    permission = "can_change_event_settings"

    def post(self, request, *args, **kwargs):
        event = request.event
        if get_scheduled_datetime(event, GO_LIVE):
            set_scheduled_datetime(event, None, GO_LIVE)
            event.log_action(
                "pretix_scheduled_live.canceled",
                user=request.user,
                data={"transition": GO_LIVE.settings_key, "scheduled_for": None},
            )
            messages.success(request, _("Scheduled publication cancelled."))
        return redirect(self._redirect_target(request))

    def _redirect_target(self, request):
        target = request.POST.get("next")
        if target and url_has_allowed_host_and_scheme(
            target, allowed_hosts=None, require_https=request.is_secure()
        ):
            return target
        return settings_url(request.event)
