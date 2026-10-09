import uuid

from django.db import migrations, models


def assign_public_tokens(apps, schema_editor):
    Student = apps.get_model("core", "Student")
    database = schema_editor.connection.alias
    for student in Student.objects.using(database).filter(public_token__isnull=True).iterator():
        Student.objects.using(database).filter(pk=student.pk).update(
            public_token=uuid.uuid4()
        )


def clear_public_tokens(apps, schema_editor):
    Student = apps.get_model("core", "Student")
    Student.objects.using(schema_editor.connection.alias).update(public_token=None)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0020_student_photo"),
    ]

    operations = [
        migrations.AddField(
            model_name="student",
            name="public_token",
            field=models.UUIDField(
                editable=False,
                help_text="Opaque identifier used only for the public student ID QR check.",
                null=True,
            ),
        ),
        migrations.RunPython(assign_public_tokens, clear_public_tokens),
        migrations.AlterField(
            model_name="student",
            name="public_token",
            field=models.UUIDField(
                default=uuid.uuid4,
                editable=False,
                help_text="Opaque identifier used only for the public student ID QR check.",
                unique=True,
            ),
        ),
    ]
