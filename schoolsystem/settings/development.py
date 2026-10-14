import os
from .base import *


SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'default-insecure-key-for-dev')
DEBUG = os.getenv('DEBUG', 'True') == 'True'

ALLOWED_HOSTS = (
    ["*"] if TENANCY_ENABLED else ["127.0.0.1", "localhost"]
)

DATABASES['default']['CONN_MAX_AGE'] = 0

INTERNAL_IPS = ['127.0.0.1']

# Keep the media backend selected in base settings while bypassing the
# manifest/collectstatic requirement for development static files.
STORAGES["staticfiles"] = {
    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
}
