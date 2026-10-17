"""Discoverable entry point for the schema-isolation integration test.

The assertions live in ``tenancy/postgres_integration.py``, whose filename does
not match Django's ``test*.py`` discovery pattern, so the runner never collected
them. Importing the test case here (in a ``test_*.py`` module) makes the
cross-tenant isolation check part of normal ``manage.py test`` and CI runs.

It self-skips unless ``TENANCY_ENABLED=true`` and PostgreSQL is in use.
"""

from .postgres_integration import TenantPostgresSchemaIntegrationTests  # noqa: F401
