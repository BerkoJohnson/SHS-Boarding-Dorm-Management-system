"""Test settings with multi-tenancy enabled (PostgreSQL required).

``schoolsystem.settings.test`` forces ``TENANCY_ENABLED=false``, which makes the
platform/tenancy suites error with "Set TENANCY_ENABLED=true before creating a
school tenant." Use this module to exercise them:

    python manage.py test tenancy --settings=schoolsystem.settings.tenancy_test

The module name deliberately does NOT start with ``test`` so Django's test
discovery (which scans for ``test*.py``) never imports it as a test module. The
``TEST`` mapping is updated in place rather than replaced so that an accidental
re-import cannot discard Django's ``MIRROR``/``MIGRATE`` defaults.
"""
import os

os.environ["TENANCY_ENABLED"] = "true"

from .development import *  # noqa: F401,F403

DATABASES["default"].setdefault("TEST", {})["NAME"] = "test_schoolsystem_tenancy"
