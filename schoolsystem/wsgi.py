"""
WSGI config for schoolsystem project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.1/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

if not os.getenv('DJANGO_SETTINGS_MODULE'):
    if os.getenv('RENDER') == 'true' or os.getenv('PORT'):
        os.environ['DJANGO_SETTINGS_MODULE'] = 'schoolsystem.settings.production'
    else:
        os.environ['DJANGO_SETTINGS_MODULE'] = 'schoolsystem.settings.development'

application = get_wsgi_application()
