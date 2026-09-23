"""
Registry of scheduled shop-status transitions.

Only "go live" is implemented for now. Scheduled *un*publication is explicitly
out of scope, but the whole plugin (settings helpers, periodic task, API fields,
UI panels) iterates over ``TRANSITIONS``, so adding it later is a matter of
appending one ``ScheduledTransition`` to this list and providing the matching
form field, template strings and translations.
"""
from dataclasses import dataclass
from typing import List

from django.utils.translation import gettext_lazy as _

#: Plugin module name, used to check whether the plugin is enabled on an event.
PLUGIN_NAME = "pretix_scheduled_live"


@dataclass(frozen=True)
class ScheduledTransition:
    #: Key of the scheduled datetime in ``event.settings``.
    settings_key: str
    #: Key holding the timestamp of the last "could not apply" notification,
    #: used to throttle e-mails to the organizer team.
    notified_key: str
    #: Value ``event.live`` must be set to when the transition fires.
    target_live: bool
    #: pretix log action recorded on success.
    log_action: str
    #: pretix log action recorded when the transition could not be applied.
    log_action_failed: str
    #: Whether ``event.live_issues`` must be empty for the transition to fire.
    requires_no_live_issues: bool
    #: Whether the transition refuses to run while the event is in test mode.
    refuses_testmode: bool
    #: Human readable label.
    label: object


GO_LIVE = ScheduledTransition(
    settings_key="scheduled_live_datetime",
    notified_key="scheduled_live_failure_notified_at",
    target_live=True,
    log_action="pretix.event.live.activated",
    log_action_failed="pretix_scheduled_live.activation_failed",
    requires_no_live_issues=True,
    refuses_testmode=True,
    label=_("Scheduled publication"),
)

#: All transitions handled by the periodic task, in evaluation order.
TRANSITIONS: List[ScheduledTransition] = [GO_LIVE]
