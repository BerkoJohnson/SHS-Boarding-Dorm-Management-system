"""Celery Beat scheduler used when background jobs are explicitly disabled.

The application still exposes Celery tasks and the web process may enqueue
work, but an installation with ``BACKGROUND_JOBS_ENABLED=False`` must not run
periodic jobs silently. This scheduler deliberately loads no schedule entries,
including rows left behind in ``django_celery_beat``.
"""

import logging

from celery.beat import Scheduler

logger = logging.getLogger(__name__)


class DisabledScheduler(Scheduler):
    """A Celery Beat scheduler that never loads or dispatches a periodic task."""

    def setup_schedule(self):
        self.data = {}
        logger.warning(
            "BACKGROUND_JOBS_ENABLED=False: Celery Beat is running but no "
            "periodic tasks will be scheduled. See README section 8."
        )
