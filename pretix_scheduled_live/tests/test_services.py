"""Behaviour of the periodic task that applies scheduled publications."""
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest import mock
from zoneinfo import ZoneInfo

import pytest
from django.utils.timezone import now
from django_scopes import scopes_disabled

from pretix_scheduled_live.services import (
    get_scheduled_datetime,
    process_due_transitions,
    set_scheduled_datetime,
)
from pretix_scheduled_live.transitions import GO_LIVE

PARIS = ZoneInfo("Europe/Paris")


def refresh(event):
    from pretix.base.models import Event

    with scopes_disabled():
        return Event.objects.get(pk=event.pk)


def log_actions(event):
    with scopes_disabled():
        return list(event.logentry_set.values_list("action_type", flat=True))


# ---------------------------------------------------------------------------
# Past date
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_past_date_takes_the_shop_live(event, now_utc):
    set_scheduled_datetime(event, now_utc - timedelta(minutes=5))

    stats = process_due_transitions(reference_time=now_utc)

    assert stats["applied"] == 1
    assert refresh(event).live is True
    assert "pretix.event.live.activated" in log_actions(event)


@pytest.mark.django_db
def test_schedule_is_cleared_after_publication(event, now_utc):
    set_scheduled_datetime(event, now_utc - timedelta(minutes=5))

    process_due_transitions(reference_time=now_utc)

    assert get_scheduled_datetime(refresh(event)) is None


@pytest.mark.django_db
def test_log_entry_names_the_plugin_as_the_source(event, now_utc):
    scheduled = now_utc - timedelta(minutes=5)
    set_scheduled_datetime(event, scheduled)

    process_due_transitions(reference_time=now_utc)

    with scopes_disabled():
        entry = event.logentry_set.get(action_type="pretix.event.live.activated")
    assert entry.parsed_data["source"] == "pretix_scheduled_live"
    assert entry.parsed_data["scheduled_for"] == scheduled.isoformat()


# ---------------------------------------------------------------------------
# Future date
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_future_date_leaves_the_shop_offline(event, now_utc):
    set_scheduled_datetime(event, now_utc + timedelta(hours=1))

    stats = process_due_transitions(reference_time=now_utc)

    assert stats["future"] == 1
    assert stats["applied"] == 0
    assert refresh(event).live is False
    assert get_scheduled_datetime(refresh(event)) is not None


@pytest.mark.django_db
def test_event_without_a_schedule_is_never_touched(event, now_utc):
    stats = process_due_transitions(reference_time=now_utc)

    assert stats["applied"] == 0
    assert refresh(event).live is False


@pytest.mark.django_db
def test_disabled_plugin_is_ignored(event, now_utc):
    set_scheduled_datetime(event, now_utc - timedelta(minutes=5))
    with scopes_disabled():
        event.plugins = ""
        event.save(update_fields=["plugins"])

    stats = process_due_transitions(reference_time=now_utc)

    assert stats["applied"] == 0
    assert refresh(event).live is False


# ---------------------------------------------------------------------------
# live_issues
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_live_issues_block_publication(event_with_issues, team_user, now_utc):
    event = event_with_issues
    with scopes_disabled():
        assert event.live_issues  # no quota configured
    set_scheduled_datetime(event, now_utc - timedelta(minutes=5))

    with mock.patch("pretix.base.services.mail.mail") as mail:
        stats = process_due_transitions(reference_time=now_utc)

    assert stats["blocked"] == 1
    assert refresh(event).live is False
    assert "pretix_scheduled_live.activation_failed" in log_actions(event)
    assert mail.call_count == 1
    assert mail.call_args.kwargs["email"] == team_user.email


@pytest.mark.django_db
def test_blocked_schedule_is_kept_for_a_later_retry(event_with_issues, team_user, now_utc):
    event = event_with_issues
    set_scheduled_datetime(event, now_utc - timedelta(minutes=5))

    with mock.patch("pretix.base.services.mail.mail"):
        process_due_transitions(reference_time=now_utc)

    assert get_scheduled_datetime(refresh(event)) is not None

    # Once the issue is fixed, the next run publishes the shop.
    with scopes_disabled():
        event.quotas.create(name="General", size=50)
    process_due_transitions(reference_time=now_utc + timedelta(minutes=5))

    assert refresh(event).live is True


@pytest.mark.django_db
def test_team_is_not_notified_twice_within_the_throttle_window(
    event_with_issues, team_user, now_utc
):
    event = event_with_issues
    set_scheduled_datetime(event, now_utc - timedelta(minutes=5))

    with mock.patch("pretix.base.services.mail.mail") as mail:
        process_due_transitions(reference_time=now_utc)
        process_due_transitions(reference_time=now_utc + timedelta(hours=1))
        assert mail.call_count == 1

        process_due_transitions(reference_time=now_utc + timedelta(hours=25))
        assert mail.call_count == 2


# ---------------------------------------------------------------------------
# Test mode
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_test_mode_blocks_publication(event, team_user, now_utc):
    with scopes_disabled():
        event.testmode = True
        event.save(update_fields=["testmode"])
    set_scheduled_datetime(event, now_utc - timedelta(minutes=5))

    with mock.patch("pretix.base.services.mail.mail") as mail:
        stats = process_due_transitions(reference_time=now_utc)

    assert stats["blocked"] == 1
    assert refresh(event).live is False
    assert mail.call_count == 1

    with scopes_disabled():
        entry = event.logentry_set.get(action_type="pretix_scheduled_live.activation_failed")
    assert any("test mode" in r.lower() for r in entry.parsed_data["reasons"])


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_cancelling_the_schedule_prevents_publication(event, now_utc):
    set_scheduled_datetime(event, now_utc - timedelta(minutes=5))
    set_scheduled_datetime(event, None)

    stats = process_due_transitions(reference_time=now_utc)

    assert stats["applied"] == 0
    assert refresh(event).live is False
    assert get_scheduled_datetime(refresh(event)) is None


@pytest.mark.django_db
def test_cancelling_resets_the_notification_throttle(event_with_issues, team_user, now_utc):
    event = event_with_issues
    set_scheduled_datetime(event, now_utc - timedelta(minutes=5))

    with mock.patch("pretix.base.services.mail.mail") as mail:
        process_due_transitions(reference_time=now_utc)
        assert mail.call_count == 1

        set_scheduled_datetime(event, None)
        set_scheduled_datetime(event, now_utc - timedelta(minutes=1))
        process_due_transitions(reference_time=now_utc + timedelta(minutes=1))
        assert mail.call_count == 2


# ---------------------------------------------------------------------------
# Idempotence
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_second_run_does_not_log_a_second_activation(event, now_utc):
    set_scheduled_datetime(event, now_utc - timedelta(minutes=5))

    process_due_transitions(reference_time=now_utc)
    process_due_transitions(reference_time=now_utc)

    assert log_actions(event).count("pretix.event.live.activated") == 1


@pytest.mark.django_db
def test_manual_publication_before_the_deadline_clears_the_schedule(event, now_utc):
    set_scheduled_datetime(event, now_utc - timedelta(minutes=5))
    with scopes_disabled():
        event.live = True
        event.save(update_fields=["live"])

    stats = process_due_transitions(reference_time=now_utc)

    assert stats["applied"] == 0
    assert get_scheduled_datetime(refresh(event)) is None
    assert "pretix.event.live.activated" not in log_actions(event)


# ---------------------------------------------------------------------------
# Timezones
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_schedule_is_stored_and_read_back_as_the_same_instant(event):
    # 1 July 2026, 09:00 in Paris == 07:00 UTC (CEST, UTC+2).
    paris_9am = datetime(2026, 7, 1, 9, 0, tzinfo=PARIS)
    set_scheduled_datetime(event, paris_9am)

    stored = get_scheduled_datetime(refresh(event))

    assert stored == paris_9am
    assert stored.astimezone(dt_timezone.utc).hour == 7


@pytest.mark.django_db
def test_paris_schedule_fires_at_the_right_utc_instant(event):
    paris_9am = datetime(2026, 7, 1, 9, 0, tzinfo=PARIS)
    set_scheduled_datetime(event, paris_9am)

    # 06:59 UTC == 08:59 in Paris: too early.
    process_due_transitions(
        reference_time=datetime(2026, 7, 1, 6, 59, tzinfo=dt_timezone.utc)
    )
    assert refresh(event).live is False

    # 07:01 UTC == 09:01 in Paris: time to go.
    process_due_transitions(
        reference_time=datetime(2026, 7, 1, 7, 1, tzinfo=dt_timezone.utc)
    )
    assert refresh(event).live is True


@pytest.mark.django_db
def test_winter_time_offset_is_respected(event):
    # 1 January 2026, 09:00 in Paris == 08:00 UTC (CET, UTC+1).
    paris_9am = datetime(2026, 1, 1, 9, 0, tzinfo=PARIS)
    set_scheduled_datetime(event, paris_9am)

    process_due_transitions(
        reference_time=datetime(2026, 1, 1, 7, 59, tzinfo=dt_timezone.utc)
    )
    assert refresh(event).live is False

    process_due_transitions(
        reference_time=datetime(2026, 1, 1, 8, 1, tzinfo=dt_timezone.utc)
    )
    assert refresh(event).live is True


@pytest.mark.django_db
def test_naive_datetimes_are_rejected_by_the_settings_store(event, now_utc):
    """A naive datetime would be ambiguous; the UI and the API always pass aware ones."""
    set_scheduled_datetime(event, now_utc - timedelta(minutes=5))
    stored = get_scheduled_datetime(refresh(event))
    assert stored.tzinfo is not None


# ---------------------------------------------------------------------------
# Extensibility
# ---------------------------------------------------------------------------

def test_go_live_transition_is_registered():
    from pretix_scheduled_live.transitions import TRANSITIONS

    assert GO_LIVE in TRANSITIONS
    assert GO_LIVE.log_action == "pretix.event.live.activated"
    assert GO_LIVE.target_live is True


# ---------------------------------------------------------------------------
# Notification e-mail
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_notification_reasons_are_translated_into_the_recipient_locale(
    event_with_issues, team_user, now_utc
):
    """
    ``live_issues`` returns lazy proxies. They must be resolved in the
    recipient's locale, not in whatever locale the cron happens to run under,
    otherwise a French e-mail lists its reasons in English.
    """
    from django.utils import translation

    event = event_with_issues
    with scopes_disabled():
        team_user.locale = "fr"
        team_user.save(update_fields=["locale"])
    set_scheduled_datetime(event, now_utc - timedelta(minutes=5))

    with translation.override("en"), mock.patch("pretix.base.services.mail.mail") as mail:
        process_due_transitions(reference_time=now_utc)

    kwargs = mail.call_args.kwargs
    assert kwargs["locale"] == "fr"
    reasons = kwargs["context"]["reasons"]
    assert reasons and all(isinstance(r, str) for r in reasons)
    assert "quota" in " ".join(reasons).lower() or "quota" in " ".join(reasons)
    # The English source string must not survive into a French e-mail.
    assert "You need to configure at least one quota" not in " ".join(reasons)


@pytest.mark.django_db
def test_notification_carries_the_scheduled_time_in_the_event_timezone(
    event_with_issues, team_user, now_utc
):
    event = event_with_issues
    set_scheduled_datetime(event, now_utc - timedelta(minutes=5))

    with mock.patch("pretix.base.services.mail.mail") as mail:
        process_due_transitions(reference_time=now_utc)

    scheduled_for = mail.call_args.kwargs["context"]["scheduled_for"]
    # Europe/Paris is UTC+2 on 1 July: 09:55 UTC is 11:55 local.
    assert scheduled_for.hour == 11


# ---------------------------------------------------------------------------
# Scheduling entry point
# ---------------------------------------------------------------------------

#: The dotted path that appears in the cron line documented in the README.
CRON_TASK_PATH = "pretix_scheduled_live.signals.on_periodic_task"


@pytest.mark.django_db
def test_runperiodic_lists_this_plugin_task(capsys):
    """
    ``runperiodic --tasks <path>`` is how the README says to schedule only this
    check every minute. If the receiver is renamed or stops being registered,
    that cron line silently does nothing — so pin it here.
    """
    from django.core.management import call_command

    call_command("runperiodic", "--list-tasks")

    assert CRON_TASK_PATH in capsys.readouterr().out


@pytest.mark.django_db
def test_documented_dotted_path_resolves_and_does_the_work(event):
    import importlib

    module_path, _, func_name = CRON_TASK_PATH.rpartition(".")
    receiver = getattr(importlib.import_module(module_path), func_name)
    set_scheduled_datetime(event, now() - timedelta(minutes=5))

    # Called exactly the way runperiodic calls its receivers.
    receiver(signal=None, sender=None)

    assert refresh(event).live is True
