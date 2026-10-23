import django.db.models.deletion
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0040_staffnotification_sms_alert"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="GroupSMSCampaign",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        help_text="Unique identifier generated automatically.",
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        help_text="Timestamp when this record was created.",
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True,
                        help_text="Timestamp when this record was last modified.",
                    ),
                ),
                (
                    "scope",
                    models.CharField(
                        choices=[
                            ("all_boarders", "All active boarders"),
                            ("house", "Boarders in one house"),
                        ],
                        max_length=20,
                    ),
                ),
                ("message", models.CharField(max_length=500)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending approval"),
                            ("approved", "Approved and queued"),
                            ("rejected", "Rejected"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=15,
                    ),
                ),
                ("recipient_count", models.PositiveIntegerField(default=0)),
                ("decision_note", models.CharField(blank=True, max_length=240)),
                ("decided_at", models.DateTimeField(blank=True, null=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="created_sms_campaigns",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "decided_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="decided_sms_campaigns",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "house",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="sms_campaigns",
                        to="core.house",
                    ),
                ),
            ],
            options={
                "verbose_name": "Group SMS Campaign",
                "verbose_name_plural": "Group SMS Campaigns",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AlterField(
            model_name="smsdelivery",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("requested", "Exeat request submitted"),
                    ("approved", "Exeat approved"),
                    ("rejected", "Exeat rejected"),
                    ("departure", "Student departed campus"),
                    ("return", "Student returned to campus"),
                    ("overdue", "Exeat overdue"),
                    ("group", "Approved group message"),
                ],
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="smsdelivery",
            name="campaign",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="deliveries",
                to="core.groupsmscampaign",
            ),
        ),
    ]
