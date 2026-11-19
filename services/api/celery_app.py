import os

from celery import Celery


redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
app = Celery(
    "trackflow",
    broker=redis_url,
    backend=redis_url,
    include=["tasks"],
)
app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    result_expires=86400,
    task_track_started=True,
    task_send_sent_event=True,
    worker_send_task_events=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_default_delivery_mode="persistent",
    broker_transport_options={"visibility_timeout": 3600},
    broker_connection_retry_on_startup=True,
)
