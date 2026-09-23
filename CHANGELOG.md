# Changelog

## 1.0.0

- Event setting “Scheduled publication” (date and time in the event timezone),
  restricted to `can_change_event_settings`.
- Periodic task that publishes the shop once the scheduled date has passed,
  using the same checks as the “Go live” button (`event.live_issues`).
- Refuses to publish an event in test mode or with unresolved live issues,
  logs the reason and notifies the organizer team by e-mail (at most once a day).
- Idempotent: overlapping runs cannot publish the same event twice.
- Entry in the event dashboard timeline, with a pencil linking to the settings tab.
- Panel on the event dashboard and on the “Shop status” page, with a cancel button.
- `scheduled_live_datetime` exposed in the event settings REST API.
- French and English translations, including the reasons listed in the
  notification e-mail, which are resolved in the recipient's locale.
