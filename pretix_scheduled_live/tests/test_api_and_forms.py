"""REST API exposure, form timezone handling and the periodic-task wiring."""
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone, translation
from django_scopes import scopes_disabled

from pretix_scheduled_live.services import get_scheduled_datetime, set_scheduled_datetime
from pretix_scheduled_live.transitions import GO_LIVE

PARIS = ZoneInfo("Europe/Paris")


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_setting_is_exposed_to_the_event_settings_api(event):
    from pretix.base.signals import api_event_settings_fields
    from rest_framework import serializers

    fields = {}
    for _recv, resp in api_event_settings_fields.send(sender=event):
        fields.update(resp)

    assert GO_LIVE.settings_key in fields
    assert isinstance(fields[GO_LIVE.settings_key], serializers.DateTimeField)
    assert fields[GO_LIVE.settings_key].allow_null is True


@pytest.mark.django_db
def test_api_style_iso_value_round_trips(event):
    """A script or an n8n workflow writes an ISO 8601 string; we read back the instant."""
    from rest_framework import serializers

    field = serializers.DateTimeField()
    value = field.to_internal_value("2026-07-01T09:00:00+02:00")
    set_scheduled_datetime(event, value)

    stored = get_scheduled_datetime(event)
    assert stored.astimezone(dt_timezone.utc) == datetime(
        2026, 7, 1, 7, 0, tzinfo=dt_timezone.utc
    )


# ---------------------------------------------------------------------------
# Form
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_form_interprets_the_entered_time_in_the_event_timezone(event):
    from pretix_scheduled_live.forms import ScheduledLiveSettingsForm

    with translation.override("fr"), timezone.override(PARIS):
        form = ScheduledLiveSettingsForm(
            data={
                "scheduled_live_datetime_0": "2026-07-01",
                "scheduled_live_datetime_1": "09:00",
            }
        )
        assert form.is_valid(), form.errors
        value = form.cleaned_data["scheduled_live_datetime"]

    assert value.astimezone(dt_timezone.utc) == datetime(
        2026, 7, 1, 7, 0, tzinfo=dt_timezone.utc
    )


@pytest.mark.django_db
def test_empty_form_value_means_no_schedule(event):
    from pretix_scheduled_live.forms import ScheduledLiveSettingsForm

    form = ScheduledLiveSettingsForm(
        data={"scheduled_live_datetime_0": "", "scheduled_live_datetime_1": ""}
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["scheduled_live_datetime"] is None


# ---------------------------------------------------------------------------
# Periodic task wiring
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_periodic_task_signal_publishes_a_due_event(event, now_utc):
    from pretix.base.signals import periodic_task

    set_scheduled_datetime(event, timezone.now() - timedelta(minutes=5))
    periodic_task.send(sender=None)

    with scopes_disabled():
        from pretix.base.models import Event

        assert Event.objects.get(pk=event.pk).live is True


# ---------------------------------------------------------------------------
# Full write path through the event settings serializer
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_event_settings_serializer_writes_and_clears_the_schedule(event):
    from pretix.api.serializers.event import EventSettingsSerializer

    with scopes_disabled():
        event.settings.set("locales", ["fr"])
        event.settings.set("locale", "fr")

        def serializer(value):
            return EventSettingsSerializer(
                instance=event.settings,
                data={GO_LIVE.settings_key: value},
                partial=True,
                event=event,
                context={"permissions": {"event.settings.general:write"}},
            )

        s = serializer("2026-11-05T20:30:00+01:00")
        assert s.is_valid(), s.errors
        s.save()
        assert get_scheduled_datetime(event).astimezone(dt_timezone.utc) == datetime(
            2026, 11, 5, 19, 30, tzinfo=dt_timezone.utc
        )

        s = serializer(None)
        assert s.is_valid(), s.errors
        s.save()
        assert get_scheduled_datetime(event) is None


@pytest.mark.django_db
def test_event_settings_serializer_requires_the_settings_permission(event):
    from pretix.api.serializers.event import EventSettingsSerializer

    with scopes_disabled():
        event.settings.set("locales", ["fr"])
        event.settings.set("locale", "fr")
        s = EventSettingsSerializer(
            instance=event.settings,
            data={GO_LIVE.settings_key: "2026-11-05T20:30:00+01:00"},
            partial=True,
            event=event,
            context={"permissions": set()},
        )
        assert not s.is_valid()
        assert GO_LIVE.settings_key in s.errors
