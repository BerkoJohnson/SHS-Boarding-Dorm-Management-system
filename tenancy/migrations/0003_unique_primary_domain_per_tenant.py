from django.db import migrations, models


class Migration(migrations.Migration):
    """Enforce at most one primary domain per tenant at the database level."""

    dependencies = [
        ("tenancy", "0002_schema_name_regex_constraint"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="domain",
            constraint=models.UniqueConstraint(
                fields=["tenant"],
                condition=models.Q(is_primary=True),
                name="unique_primary_domain_per_tenant",
            ),
        ),
    ]
