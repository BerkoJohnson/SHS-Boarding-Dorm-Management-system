from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0039_alter_auditevent_action_staffinvite_apiaccesstoken_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="staffnotification",
            name="kind",
            field=models.CharField(
                choices=[
                    ("late_return", "Late return"),
                    ("exeat_review", "Exeat review"),
                    ("roll_call_correction", "Roll-call correction"),
                    ("dispute", "Dispute"),
                    ("sms_alert", "SMS delivery alert"),
                    ("general", "General"),
                ],
                default="general",
                max_length=30,
            ),
        ),
    ]
