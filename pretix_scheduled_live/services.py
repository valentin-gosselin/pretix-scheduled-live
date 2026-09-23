"""
Core logic: apply the scheduled shop-status transitions that are due.

Everything in here is designed to be idempotent. Two overlapping runs cannot
publish the same event twice because each event is processed inside a
transaction that takes a row lock on the event and re-reads both ``event.live``
and the scheduled datetime *after* acquiring the lock.
"""
import logging
from collections import Counter
from datetime import datetime, timedelta

from django.db import transaction
from django.utils.timezone import now
from django.utils.translation import gettext, gettext_lazy as _
from django_scopes import scope, scopes_disabled
from pretix.helpers.periodic import minimum_interval

from .transitions import PLUGIN_NAME, TRANSITIONS, ScheduledTransition

logger = logging.getLogger(__name__)

#: Do not e-mail the organizer team about the same blocked transition more
#: often than this.
NOTIFICATION_THROTTLE = timedelta(hours=24)

MAIL_TEMPLATE = "pretix_scheduled_live/mail_activation_failed.txt"


def plugin_is_active(event) -> bool:
    return PLUGIN_NAME in (event.plugins or "")


def get_scheduled_datetime(event, transition: ScheduledTransition = None):
    """Return the scheduled datetime for ``transition``, or ``None``."""
    transition = transition or TRANSITIONS[0]
    return event.settings.get(transition.settings_key, as_type=datetime)


def set_scheduled_datetime(event, value, transition: ScheduledTransition = None):
    """Store (or clear, when ``value`` is falsy) the scheduled datetime."""
    transition = transition or TRANSITIONS[0]
    if value:
        event.settings.set(transition.settings_key, value)
    else:
        event.settings.delete(transition.settings_key)
    # A new (or removed) schedule always resets the notification throttle.
    event.settings.delete(transition.notified_key)


def blocking_reasons(event, transition: ScheduledTransition):
    """
    Return the list of reasons preventing ``transition`` from being applied now.

    Uses exactly the same checks as pretix' own "Go live" button
    (``event.live_issues``), plus the test-mode guard.
    """
    reasons = []
    if transition.refuses_testmode and event.testmode:
        reasons.append(_("The event is in test mode."))
    if transition.requires_no_live_issues:
        reasons.extend(event.live_issues)
    return reasons


@minimum_interval(minutes_after_success=0, minutes_after_error=0)
def run_due_transitions_guarded():
    """
    ``process_due_transitions`` behind pretix' cache-based "not already running"
    guard.

    Correctness does not depend on this guard — each event is processed under a
    database row lock — but it avoids piling up workers when the periodic task
    is scheduled every minute.
    """
    return process_due_transitions()


def process_due_transitions(reference_time=None) -> Counter:
    """
    Apply every scheduled transition whose datetime has passed.

    Returns a ``Counter`` with the keys ``applied``, ``blocked``, ``skipped``
    and ``future`` — handy for tests and logging.
    """
    reference_time = reference_time or now()
    stats = Counter()

    with scopes_disabled():
        from pretix.base.models import Event_SettingsStore

        for transition in TRANSITIONS:
            event_ids = list(
                Event_SettingsStore.objects.filter(key=transition.settings_key)
                .exclude(value="")
                .values_list("object_id", flat=True)
            )
            for event_id in event_ids:
                try:
                    stats[_process_event(event_id, transition, reference_time)] += 1
                except Exception:
                    # One broken event must never stop the others.
                    logger.exception(
                        "pretix-scheduled-live: failed to process event %s for transition %s",
                        event_id,
                        transition.settings_key,
                    )
                    stats["errored"] += 1

    return stats


def _process_event(event_id, transition: ScheduledTransition, reference_time) -> str:
    from pretix.base.models import Event
    from pretix.helpers.database import OF_SELF

    with transaction.atomic():
        try:
            event = (
                Event.objects.select_for_update(of=OF_SELF)
                .select_related("organizer")
                .get(pk=event_id)
            )
        except Event.DoesNotExist:
            return "skipped"

        if not plugin_is_active(event):
            return "skipped"

        # Re-read *after* taking the lock: a concurrent run may already have
        # applied this transition and cleared the setting.
        scheduled = get_scheduled_datetime(event, transition)
        if not scheduled:
            return "skipped"
        if scheduled > reference_time:
            return "future"

        if event.live == transition.target_live:
            # Someone did it by hand in the meantime — nothing to do, but the
            # schedule is stale, so drop it.
            set_scheduled_datetime(event, None, transition)
            return "skipped"

        reasons = blocking_reasons(event, transition)
        if reasons:
            _handle_blocked(event, transition, scheduled, reasons, reference_time)
            return "blocked"

        event.live = transition.target_live
        event.save(update_fields=["live"])
        with scope(organizer=event.organizer):
            event.log_action(
                transition.log_action,
                data={
                    "source": PLUGIN_NAME,
                    "scheduled_for": scheduled.isoformat(),
                },
            )
        set_scheduled_datetime(event, None, transition)

    logger.info(
        "pretix-scheduled-live: %s/%s taken live as scheduled for %s",
        event.organizer.slug,
        event.slug,
        scheduled.isoformat(),
    )
    return "applied"


def _handle_blocked(event, transition, scheduled, reasons, reference_time):
    """Log the refusal and e-mail the organizer team (at most once a day)."""
    reason_texts = [str(r) for r in reasons]

    with scope(organizer=event.organizer):
        event.log_action(
            transition.log_action_failed,
            data={
                "source": PLUGIN_NAME,
                "scheduled_for": scheduled.isoformat(),
                "reasons": reason_texts,
            },
        )

    logger.warning(
        "pretix-scheduled-live: %s/%s not taken live, scheduled for %s, reasons: %s",
        event.organizer.slug,
        event.slug,
        scheduled.isoformat(),
        "; ".join(reason_texts),
    )

    last_notified = event.settings.get(transition.notified_key, as_type=datetime)
    if last_notified and reference_time - last_notified < NOTIFICATION_THROTTLE:
        return

    _notify_team(event, transition, scheduled, reasons)
    event.settings.set(transition.notified_key, reference_time)


def _notify_team(event, transition, scheduled, reasons):
    from django.utils import translation
    from pretix.base.services.mail import SendMailException, mail

    with scope(organizer=event.organizer):
        recipients = list(event.get_users_with_permission("can_change_event_settings"))

    for user in recipients:
        locale = user.locale or event.settings.locale
        # ``live_issues`` yields lazy translation proxies. Resolving them inside
        # the override - rather than once, in whatever locale the cron happens
        # to run under - is what keeps a French e-mail entirely in French.
        with translation.override(locale):
            subject = gettext(
                "Ticket shop “{event}” could not be published as scheduled"
            ).format(event=str(event.name))
            context = {
                "event": event,
                "event_name": str(event.name),
                "organizer_name": str(event.organizer.name),
                "scheduled_for": scheduled.astimezone(event.timezone),
                "reasons": [str(r) for r in reasons],
                "live_url": _control_live_url(event),
            }
        try:
            mail(
                email=user.email,
                subject=subject,
                template=MAIL_TEMPLATE,
                context=context,
                event=event,
                locale=locale,
                user=user,
            )
        except SendMailException:
            logger.exception(
                "pretix-scheduled-live: could not notify %s about %s/%s",
                user.email,
                event.organizer.slug,
                event.slug,
            )


def _control_live_url(event):
    from django.conf import settings as django_settings
    from django.urls import reverse

    path = reverse(
        "control:event.live",
        kwargs={"organizer": event.organizer.slug, "event": event.slug},
    )
    base = getattr(django_settings, "SITE_URL", "").rstrip("/")
    return f"{base}{path}"
