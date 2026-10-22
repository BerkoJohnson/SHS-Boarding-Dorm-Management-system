from django.db import migrations, models


def keep_latest_active_term_per_level(apps, schema_editor):
    AcademicTerm = apps.get_model("core", "AcademicTerm")
    manager = AcademicTerm.objects.using(schema_editor.connection.alias)
    levels = list(
        manager.filter(is_current=True)
        .values_list("level", flat=True)
        .distinct()
    )
    for level in levels:
        active_terms = list(
            manager.filter(level=level, is_current=True)
            .order_by("-start_date", "-created_at", "-pk")
            .values_list("pk", flat=True)
        )
        if len(active_terms) > 1:
            manager.filter(pk__in=active_terms[1:]).update(is_current=False)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0036_disputecase"),
    ]

    operations = [
        migrations.RunPython(
            keep_latest_active_term_per_level,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="academicterm",
            constraint=models.UniqueConstraint(
                fields=("level",),
                condition=models.Q(("is_current", True)),
                name="unique_current_academic_term_per_level",
            ),
        ),
        migrations.AlterField(
            model_name="auditevent",
            name="action",
            field=models.CharField(
                choices=[
                    ("exeat_cancelled", "Exeat cancelled"),
                    ("exeat_rescheduled", "Exeat rescheduled"),
                    ("emergency_override", "Emergency override used"),
                    ("exeat_review_decided", "Exeat review decided"),
                    ("gate_duty_changed", "Gate duty assignment changed"),
                    ("late_return_opened", "Late-return incident opened"),
                    ("late_return_updated", "Late-return incident updated"),
                    (
                        "roll_call_correction_requested",
                        "Roll-call correction requested",
                    ),
                    (
                        "roll_call_correction_decided",
                        "Roll-call correction decided",
                    ),
                    (
                        "dormitory_capacity_exception",
                        "Dormitory capacity exception",
                    ),
                    ("export", "Sensitive report export"),
                    ("admin_edit", "Administrative edit"),
                    ("dispute", "Dispute case changed"),
                ],
                max_length=40,
            ),
        ),
    ]
