import django.utils.timezone
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0011_exeatreconciliation'),
    ]

    operations = [
        migrations.CreateModel(
            name='SMSDelivery',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, help_text='Unique identifier generated automatically.', primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, help_text='Timestamp when this record was created.')),
                ('updated_at', models.DateTimeField(auto_now=True, help_text='Timestamp when this record was last modified.')),
                ('event_type', models.CharField(choices=[('requested', 'Exeat request submitted'), ('approved', 'Exeat approved'), ('rejected', 'Exeat rejected'), ('departure', 'Student departed campus'), ('return', 'Student returned to campus'), ('overdue', 'Exeat overdue')], max_length=30)),
                ('recipient_name', models.CharField(blank=True, max_length=150)),
                ('phone_number', models.CharField(db_index=True, max_length=17)),
                ('message', models.TextField()),
                ('status', models.CharField(choices=[('queued', 'Queued'), ('processing', 'Processing'), ('sent', 'Sent'), ('failed', 'Failed')], default='queued', max_length=15)),
                ('provider_reference', models.CharField(blank=True, max_length=200)),
                ('attempts', models.PositiveIntegerField(default=0)),
                ('last_attempted_at', models.DateTimeField(blank=True, null=True)),
                ('sent_at', models.DateTimeField(blank=True, null=True)),
                ('last_error', models.TextField(blank=True)),
                ('dedupe_key', models.CharField(db_index=True, max_length=200, unique=True)),
                ('scheduled_for', models.DateTimeField(default=django.utils.timezone.now)),
                ('exeat', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='sms_deliveries', to='core.exeat')),
            ],
            options={
                'ordering': ['-created_at'],
                'verbose_name': 'SMS Delivery',
                'verbose_name_plural': 'SMS Deliveries',
            },
        ),
    ]
