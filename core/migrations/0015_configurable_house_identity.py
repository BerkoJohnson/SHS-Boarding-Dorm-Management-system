from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0014_schoolconfiguration"),
    ]

    operations = [
        migrations.RenameField(
            model_name="house",
            old_name="number",
            new_name="display_order",
        ),
        migrations.AlterField(
            model_name="house",
            name="display_order",
            field=models.PositiveSmallIntegerField(
                default=0,
                help_text="Optional ordering for house lists. Houses with the same value are sorted by name.",
            ),
        ),
        migrations.AlterModelOptions(
            name="house",
            options={"ordering": ["display_order", "name"]},
        ),
    ]
