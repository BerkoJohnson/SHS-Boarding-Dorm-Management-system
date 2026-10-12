import os

from celery import Celery


if not os.getenv('DJANGO_SETTINGS_MODULE'):
    if os.getenv('RENDER') == 'true' or os.getenv('PORT'):
        os.environ['DJANGO_SETTINGS_MODULE'] = 'schoolsystem.settings.production'
    else:
        os.environ['DJANGO_SETTINGS_MODULE'] = 'schoolsystem.settings.development'

app = Celery("schoolsystem")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
