from django import forms
from django.utils.translation import gettext_lazy as _
from pretix.base.forms.widgets import SplitDateTimePickerWidget
from pretix.control.forms import SplitDateTimeField

from .transitions import GO_LIVE


class ScheduledLiveSettingsForm(forms.Form):
    """
    One field per transition. ``SplitDateTimeField`` interprets the entered
    value in the currently active timezone, which pretix sets to the event
    timezone for every control request.
    """

    scheduled_live_datetime = SplitDateTimeField(
        label=_("Scheduled publication"),
        required=False,
        widget=SplitDateTimePickerWidget(),
        help_text=_(
            "Date and time, in the event timezone, at which the ticket shop should "
            "automatically go live. Leave empty to disable scheduled publication."
        ),
    )

    #: Maps a form field to the transition it schedules.
    FIELD_TRANSITIONS = {
        "scheduled_live_datetime": GO_LIVE,
    }
