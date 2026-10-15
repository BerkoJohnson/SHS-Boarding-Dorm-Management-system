from django.db import migrations


class Migration(migrations.Migration):
    """Add a database-level regex constraint on schema_name to prevent
    hyphens and other characters that would break PostgreSQL search_path
    SQL syntax."""

    dependencies = [
        ("tenancy", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                "ALTER TABLE tenancy_tenant "
                "ADD CONSTRAINT ck_schema_name_valid_identifier "
                "CHECK (schema_name ~ '^[a-z][a-z0-9_]*$');"
            ),
            reverse_sql=(
                "ALTER TABLE tenancy_tenant "
                "DROP CONSTRAINT IF EXISTS ck_schema_name_valid_identifier;"
            ),
        ),
    ]
