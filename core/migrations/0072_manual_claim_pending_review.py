"""ManualSearchClaim: добавить статус PENDING + поля reviewed_by/at для
ручной модерации админом."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0071_manual_search_claim'),
    ]

    operations = [
        migrations.AlterField(
            model_name='manualsearchclaim',
            name='status',
            field=models.CharField(
                max_length=20, db_index=True,
                choices=[
                    ('pending', 'На проверке'),
                    ('approved', 'Одобрено'),
                    ('rejected', 'Отклонено'),
                ],
            ),
        ),
        migrations.AddField(
            model_name='manualsearchclaim',
            name='reviewed_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='reviewed_manual_claims',
                to=settings.AUTH_USER_MODEL,
                help_text='Админ, проверивший заявку (null если auto-reject из-за дубликата).',
            ),
        ),
        migrations.AddField(
            model_name='manualsearchclaim',
            name='reviewed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
