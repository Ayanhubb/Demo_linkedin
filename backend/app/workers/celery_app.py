"""Celery application. Broker, timezone, and beat schedule come from the environment."""

from celery import Celery
from celery.signals import setup_logging

from app.core.config import get_settings
from app.core.logging import configure_logging

configure_logging()
_settings = get_settings()

celery_app = Celery(
    "linkedin_scheduler",
    broker=_settings.redis_url,
)
celery_app.conf.update(
    timezone="UTC",
    enable_utc=True,
    task_ignore_result=True,
    task_acks_late=False,
    worker_hijack_root_logger=False,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        "process-due-posts": {
            "task": "app.workers.tasks.process_due_posts",
            "schedule": _settings.celery_beat_interval_seconds,
        }
    },
)


@setup_logging.connect
def _configure_worker_logging(**_kwargs: object) -> None:
    configure_logging()


import app.workers.tasks as _tasks  # noqa: E402,F401

del _tasks
