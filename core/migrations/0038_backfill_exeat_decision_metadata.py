from django.db import migrations, models


def backfill_exeat_decision_metadata(apps, schema_editor):
    """Populate decision timestamps and author users for existing exeats."""
    Exeat = apps.get_model("core", "Exeat")
    exeats = Exeat.objects.using(schema_editor.connection.alias)
    exeats.exclude(status="Pending").filter(decided_at__isnull=True).update(
        decided_at=models.F("updated_at")
    )
    for exeat in exeats.filter(
        approved_by__isnull=False,
        approved_by_user__isnull=True,
    ).select_related("approved_by"):
        user_id = getattr(exeat.approved_by, "user_id", None)
        if user_id:
            exeats.filter(pk=exeat.pk).update(approved_by_user_id=user_id)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0037_current_term_guard"),
    ]

    operations = [
        migrations.RunPython(
            backfill_exeat_decision_metadata,
            migrations.RunPython.noop,
        ),
    ]
