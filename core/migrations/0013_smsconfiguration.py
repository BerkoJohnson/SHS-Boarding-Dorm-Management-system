from django.conf import settings
from django.db import migrations, models


def create_sms_configuration(apps, schema_editor):
    SMSConfiguration = apps.get_model("core", "SMSConfiguration")
    SMSConfiguration.objects.using(schema_editor.connection.alias).get_or_create(
        id=1,
        defaults={"enabled": getattr(settings, "SMS_ENABLED", False)},
    )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0012_smsdelivery"),
    ]

    operations = [
        migrations.CreateModel(
            name="SMSConfiguration",
            fields=[
                (
                    "id",
                    models.PositiveSmallIntegerField(
                        default=1,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "enabled",
                    models.BooleanField(
                        default=False,
                        help_text="When disabled, no new SMS notifications will be sent.",
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "SMS Configuration",
                "verbose_name_plural": "SMS Configuration",
            },
        ),
        migrations.RunPython(
            create_sms_configuration,
            migrations.RunPython.noop,
        ),
    ]
