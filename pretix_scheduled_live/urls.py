from django.urls import path

from . import views

urlpatterns = [
    path(
        "control/event/<str:organizer>/<str:event>/settings/scheduled-live/",
        views.ScheduledLiveSettingsView.as_view(),
        name="settings",
    ),
    path(
        "control/event/<str:organizer>/<str:event>/settings/scheduled-live/cancel",
        views.ScheduledLiveCancelView.as_view(),
        name="cancel",
    ),
]
