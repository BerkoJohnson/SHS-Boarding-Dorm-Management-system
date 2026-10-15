import os

from celery import Celery
from celery.signals import task_postrun, task_prerun, worker_process_init


if not os.getenv('DJANGO_SETTINGS_MODULE'):
    if os.getenv('RENDER') == 'true' or os.getenv('PORT'):
        os.environ['DJANGO_SETTINGS_MODULE'] = 'schoolsystem.settings.production'
    else:
        os.environ['DJANGO_SETTINGS_MODULE'] = 'schoolsystem.settings.development'

app = Celery("schoolsystem")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@worker_process_init.connect
def close_db_connections_on_worker_init(**kwargs):
    """
    Close all database connections inherited from the parent process
    after a prefork worker process is spawned.

    Prevents worker child processes from sharing the same underlying
    socket descriptors to PostgreSQL.
    """
    from django.db import connections

    for conn in connections.all():
        conn.close()


@task_prerun.connect
def reset_connections_and_schema_on_task_prerun(**kwargs):
    """
    Clean up idle or stale connections before executing a task, and ensure
    the worker starts from a clean 'public' schema baseline.
    """
    from django.conf import settings
    from django.db import close_old_connections

    close_old_connections()
    if getattr(settings, "TENANCY_ENABLED", False):
        try:
            from django_pgschemas.schema import activate_public

            activate_public()
        except ImportError:
            pass


@task_postrun.connect
def reset_connections_and_schema_on_task_postrun(**kwargs):
    """
    Guarantee schema reset to 'public' and close expired connections after
    every task, preventing cross-tenant search_path contamination across
    long-running persistent worker processes.
    """
    from django.conf import settings
    from django.db import close_old_connections

    if getattr(settings, "TENANCY_ENABLED", False):
        try:
            from django_pgschemas.schema import activate_public

            activate_public()
        except ImportError:
            pass
    close_old_connections()

