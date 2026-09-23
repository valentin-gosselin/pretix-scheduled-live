# pretix-scheduled-live

[![CI](https://github.com/valentin-gosselin/pretix-scheduled-live/actions/workflows/ci.yml/badge.svg)](https://github.com/valentin-gosselin/pretix-scheduled-live/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Schedule the exact date and time at which a pretix ticket shop goes live.

## Why

Taking a shop live in pretix is a manual click. There is no way to say
"publish this on 1 October at 09:00".

`presale_start` is not a substitute. It controls when tickets can be *bought*,
but the shop page itself is already public before that: visitors can see the
event, the products and the prices, and search engines can index the page. If
you want the announcement, the WordPress post and the ticket shop to all appear
at the same minute, that does not work.

This plugin keeps the shop **completely invisible** until the scheduled instant,
then takes it live automatically — running the same safety checks as the
"Go live" button.

## Compatibility

| | |
|---|---|
| pretix | 2024.1 and later. Tested against 2026.5.4 and 2026.7.0. |
| Python | 3.10+ |
| Database | PostgreSQL, SQLite, MySQL/MariaDB |

No database migration: the scheduled date lives in `event.settings`.

## Features

- **Event setting “Scheduled publication”** — date and time in the event timezone,
  under *Settings → Scheduled publication*, restricted to `can_change_event_settings`.
  Stored in `event.settings`, not in a new model.
- **Periodic task** (`periodic_task` signal). When the scheduled date has passed and
  the event is not live yet:
  - it runs `event.live_issues`, i.e. exactly the same checks as the “Go live”
    button (quotas, payment providers, required meta properties, plugin checks);
  - if there is no issue, it sets `event.live = True` and logs
    `pretix.event.live.activated` with `source = pretix_scheduled_live`, then clears
    the scheduled date;
  - if there are issues, or if the event is in **test mode**, it does *not* publish.
    It logs `pretix_scheduled_live.activation_failed` with the reasons and e-mails the
    organizer team (at most once every 24 h). The schedule is kept, so the shop is
    published automatically as soon as the issues are fixed.
- **Idempotent.** Each event is processed inside a transaction holding a row lock on
  the event; `event.live` and the scheduled date are re-read after the lock is taken.
  Two overlapping cron runs cannot publish the same event twice.
- **Timeline entry.** The scheduled publication shows up in the dashboard timeline
  (“Your timeline”), between the presale start and the event date, with a pencil
  linking to the settings tab — exactly how core handles `presale_start`.
- **Panels** on the event dashboard and on the “Shop status” page showing the
  scheduled date, with a cancel button. Saving a date immediately warns about any
  existing `live_issues`.
- **REST API**: `scheduled_live_datetime` is exposed through
  `api_event_settings_fields`, so it can be set from a script or from n8n:

  ```http
  PATCH /api/v1/organizers/{org}/events/{event}/settings/
  {"scheduled_live_datetime": "2026-10-01T09:00:00+02:00"}
  ```

  Send `null` to cancel. A full example:

  ```bash
  curl -X PATCH \
    -H "Authorization: Token $PRETIX_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"scheduled_live_datetime": "2026-10-01T09:00:00+02:00"}' \
    https://pretix.example.com/api/v1/organizers/acme/events/concert/settings/
  ```

  Writing the field requires the `event.settings.general:write` permission, the
  same one the "Go live" button requires. This is what makes the plugin usable
  from n8n, a deployment script or a CMS hook.
- **Translated interface.** English and French are maintained by hand; German,
  Spanish, Italian, Dutch, Portuguese and Polish are machine-assisted and have
  not been reviewed by native speakers — corrections are very welcome.

## Out of scope (for now)

Scheduled *un*publication is deliberately not implemented. The whole plugin iterates
over `pretix_scheduled_live.transitions.TRANSITIONS`, so adding it later means
appending one `ScheduledTransition` (plus a form field and the matching strings).

## Installation

```bash
pip install git+https://github.com/valentin-gosselin/pretix-scheduled-live.git@main
```

Then enable *Scheduled shop publication* in the event's plugin list.

### Scheduling — important

The plugin hooks into `periodic_task`, the signal pretix sends every time the
`runperiodic` cron runs. **pretix does not run that cron for you** — there is no
scheduler process in the Docker image, you schedule it on the host — and the
pretix documentation recommends "something between every minute and every hour".

Whatever interval you use is also the worst-case delay for a scheduled
publication. At `*/30`, a shop scheduled for 09:00 goes live at 09:30. If the
point is to line the publication up with a scheduled WordPress post, pick one of:

```cron
# Option A — everything every minute. Endorsed by the pretix docs; pretix' own
# periodic receivers self-throttle with @minimum_interval, so this is cheap.
* * * * * docker exec pretix python -m pretix runperiodic

# Option B — keep your existing coarse cron and add a targeted one-liner.
# This receiver takes about 6 ms when there is nothing to do.
* * * * * docker exec pretix python -m pretix runperiodic --tasks pretix_scheduled_live.signals.on_periodic_task
```

Both are idempotent and safe to combine. With either, a shop goes live within a
few seconds of the scheduled time.

`runperiodic --list-tasks` shows every registered task if you want to check.

## What happens at the scheduled time

| Situation | Result |
|---|---|
| No issue | Shop goes live, `pretix.event.live.activated` logged, schedule cleared |
| `live_issues` not empty | Nothing published, reason logged, team e-mailed, **schedule kept** |
| Event in test mode | Nothing published, reason logged, team e-mailed, schedule kept |
| Already live | Schedule silently cleared, nothing logged |
| Plugin disabled on the event | Ignored |

Because the schedule is kept when publication is refused, fixing the issue is
enough: the shop goes live on the next run, with no further action. The team is
e-mailed at most once every 24 hours per event, so a long-standing problem does
not turn into a mailbox flood.

## Development

```bash
make test-docker           # run the test suite inside the pretix-dev container
make deploy                # build a wheel and install it in pretix-dev
make update                # extract + compile translations
make build                 # build a wheel into dist/
```

The tests need a pretix container, since they run against `pretix.testutils.settings`:

```bash
docker exec -w /plugins/pretix-scheduled-live pretix-dev \
    python -m pytest -q pretix_scheduled_live/tests/
```

## Contributing

Issues and pull requests are welcome, in particular:

- translation fixes for the machine-assisted languages listed above;
- scheduled *un*publication, which the `transitions.py` registry is designed for.

## Licence

MIT — see [LICENSE](LICENSE).
