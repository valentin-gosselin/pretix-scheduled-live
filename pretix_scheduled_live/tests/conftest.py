"""
Test fixtures for pretix-scheduled-live.

Run them from inside a pretix container, which ships ``pretix.testutils.settings``::

    docker exec pretix-dev python -m pytest /plugins/pretix-scheduled-live/pretix_scheduled_live/tests/
"""
import os
from datetime import datetime, timezone as dt_timezone

import pytest


def pytest_configure(config):  # noqa: ARG001
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pretix.testutils.settings")
    import django

    django.setup()


#: Fixed "now" used by every test, in UTC. Paris is UTC+2 on that date.
NOW = datetime(2026, 7, 1, 10, 0, 0, tzinfo=dt_timezone.utc)


@pytest.fixture
def now_utc():
    return NOW


@pytest.fixture
def organizer(db):
    from django_scopes import scopes_disabled
    from pretix.base.models import Organizer

    with scopes_disabled():
        return Organizer.objects.create(name="Gosselico", slug="gosselico-test")


@pytest.fixture
def event(db, organizer):
    """A sellable, publishable event with the plugin enabled — no live issues."""
    from django_scopes import scopes_disabled
    from pretix.base.models import Event

    with scopes_disabled():
        event = Event.objects.create(
            organizer=organizer,
            name="Concert de test",
            slug="concert-test",
            date_from=datetime(2026, 12, 31, 19, 0, 0, tzinfo=dt_timezone.utc),
            live=False,
            plugins="pretix_scheduled_live",
        )
        event.settings.set("timezone", "Europe/Paris")
        # A quota is the minimum pretix requires to consider a shop publishable.
        event.quotas.create(name="General", size=100)
        return event


@pytest.fixture
def event_with_issues(db, organizer):
    """Same as ``event`` but without any quota, so ``live_issues`` is non-empty."""
    from django_scopes import scopes_disabled
    from pretix.base.models import Event

    with scopes_disabled():
        event = Event.objects.create(
            organizer=organizer,
            name="Concert sans quota",
            slug="concert-sans-quota",
            date_from=datetime(2026, 12, 31, 19, 0, 0, tzinfo=dt_timezone.utc),
            live=False,
            plugins="pretix_scheduled_live",
        )
        event.settings.set("timezone", "Europe/Paris")
        return event


@pytest.fixture
def team_user(db, organizer):
    """A user allowed to change event settings — the notification recipient."""
    from django_scopes import scopes_disabled
    from pretix.base.models import Team, User

    with scopes_disabled():
        user = User.objects.create_user(
            email="equipe@gosselico.fr", password="dummy-password-for-tests"
        )
        kwargs = {"organizer": organizer, "name": "Équipe", "all_events": True}
        # pretix 2026.5 replaced the per-permission booleans by a JSON field.
        field_names = {f.name for f in Team._meta.get_fields()}
        if "all_event_permissions" in field_names:
            kwargs["all_event_permissions"] = True
        else:
            kwargs["can_change_event_settings"] = True
        team = Team.objects.create(**kwargs)
        team.members.add(user)
        return user
