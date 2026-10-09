from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0022_student_id_exports"),
    ]

    operations = [
        migrations.AlterField(
            model_name="schoolconfiguration",
            name="name",
            field=models.CharField(default="ATEBUBU SENIOR HIGH SCHOOL", max_length=150),
        ),
        migrations.AddField(
            model_name="schoolconfiguration",
            name="short_name",
            field=models.CharField(
                blank=True,
                default="ATSEC",
                help_text="Short school name used in SMS notifications.",
                max_length=50,
            ),
        ),
    ]
