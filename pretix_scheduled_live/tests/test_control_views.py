"""
Control-panel rendering.

These tests lock in two bugs found while smoke-testing 1.0.0 against a real
instance:

* the dashboard panel must not rely on ``event_dashboard_widgets``, which is
  sent *without* the request;
* the "Shop status" panel is injected from a template context processor, so it
  must be rendered without ``request=``, otherwise every context processor runs
  again and the page dies with a ``RecursionError``.
"""
import re
from datetime import timedelta

import pytest
from django.test import Client
from django.utils.timezone import now
from django_scopes import scopes_disabled

from pretix_scheduled_live.services import get_scheduled_datetime, set_scheduled_datetime

PANEL_MARKER = "pretix-scheduled-live-panel"


@pytest.fixture
def client_logged_in(team_user):
    client = Client()
    client.force_login(team_user)
    return client


def base_url(event):
    return f"/control/event/{event.organizer.slug}/{event.slug}"


def csrf_token(html):
    match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html)
    return match.group(1) if match else None


@pytest.mark.django_db
def test_settings_page_renders(event, client_logged_in):
    response = client_logged_in.get(f"{base_url(event)}/settings/scheduled-live/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_settings_tab_is_listed(event, client_logged_in):
    response = client_logged_in.get(f"{base_url(event)}/settings/")
    assert "/settings/scheduled-live/" in response.content.decode()


@pytest.mark.django_db
def test_shop_status_page_shows_the_panel(event, client_logged_in):
    set_scheduled_datetime(event, now() + timedelta(days=1))

    response = client_logged_in.get(f"{base_url(event)}/live/")

    assert response.status_code == 200
    assert PANEL_MARKER in response.content.decode()


@pytest.mark.django_db
def test_dashboard_shows_the_panel(event, client_logged_in):
    set_scheduled_datetime(event, now() + timedelta(days=1))

    response = client_logged_in.get(f"{base_url(event)}/")

    assert response.status_code == 200
    assert PANEL_MARKER in response.content.decode()


@pytest.mark.django_db
def test_no_panel_without_a_schedule(event, client_logged_in):
    response = client_logged_in.get(f"{base_url(event)}/live/")

    assert response.status_code == 200
    assert PANEL_MARKER not in response.content.decode()


@pytest.mark.django_db
def test_cancel_button_clears_the_schedule(event, client_logged_in):
    set_scheduled_datetime(event, now() + timedelta(days=1))
    live_page = client_logged_in.get(f"{base_url(event)}/live/").content.decode()
    token = csrf_token(live_page)
    assert token, "the injected panel must carry a CSRF token"

    response = client_logged_in.post(
        f"{base_url(event)}/settings/scheduled-live/cancel",
        {"csrfmiddlewaretoken": token, "next": f"{base_url(event)}/live/"},
    )

    assert response.status_code == 302
    assert response["Location"] == f"{base_url(event)}/live/"
    with scopes_disabled():
        from pretix.base.models import Event

        assert get_scheduled_datetime(Event.objects.get(pk=event.pk)) is None


@pytest.mark.django_db
def test_cancel_refuses_an_external_redirect(event, client_logged_in):
    set_scheduled_datetime(event, now() + timedelta(days=1))
    page = client_logged_in.get(f"{base_url(event)}/settings/scheduled-live/").content.decode()

    response = client_logged_in.post(
        f"{base_url(event)}/settings/scheduled-live/cancel",
        {"csrfmiddlewaretoken": csrf_token(page), "next": "https://evil.example.com/"},
    )

    assert response["Location"] == f"{base_url(event)}/settings/scheduled-live/"


@pytest.mark.django_db
def test_saving_the_form_stores_the_event_timezone_instant(event, client_logged_in):
    from datetime import datetime
    from datetime import timezone as dt_timezone

    page = client_logged_in.get(f"{base_url(event)}/settings/scheduled-live/").content.decode()
    response = client_logged_in.post(
        f"{base_url(event)}/settings/scheduled-live/",
        {
            "csrfmiddlewaretoken": csrf_token(page),
            "scheduled_live_datetime_0": "2026-10-01",
            "scheduled_live_datetime_1": "09:00",
        },
    )

    assert response.status_code == 302
    with scopes_disabled():
        from pretix.base.models import Event

        stored = get_scheduled_datetime(Event.objects.get(pk=event.pk))
    # Europe/Paris is UTC+2 on 1 October.
    assert stored.astimezone(dt_timezone.utc) == datetime(
        2026, 10, 1, 7, 0, tzinfo=dt_timezone.utc
    )


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_schedule_appears_in_the_timeline(event, client_logged_in):
    from datetime import datetime
    from datetime import timezone as dt_timezone

    from pretix.base.timeline import timeline_for_event

    scheduled = datetime(2026, 10, 1, 7, 0, tzinfo=dt_timezone.utc)
    set_scheduled_datetime(event, scheduled)

    with scopes_disabled():
        entries = timeline_for_event(event)

    ours = [e for e in entries if e.datetime == scheduled]
    assert len(ours) == 1
    assert "live" in str(ours[0].description).lower()
    assert ours[0].edit_url.endswith("/settings/scheduled-live/")


@pytest.mark.django_db
def test_timeline_is_untouched_without_a_schedule(event):
    from pretix.base.timeline import timeline_for_event

    with scopes_disabled():
        entries = timeline_for_event(event)

    assert all("/settings/scheduled-live/" not in (e.edit_url or "") for e in entries)


@pytest.mark.django_db
def test_timeline_entry_is_rendered_on_the_dashboard(event, client_logged_in):
    set_scheduled_datetime(event, now() + timedelta(days=1))

    body = client_logged_in.get(f"{base_url(event)}/").content.decode()

    assert "/settings/scheduled-live/" in body
