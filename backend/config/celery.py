import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("config")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.beat_schedule = {
    "dispatch-outbox-events": {
        "task": "reconciliation.tasks.dispatch_outbox_events",
        "schedule": 2.0,  # every 2 seconds
    },
}
