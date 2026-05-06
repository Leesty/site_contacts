"""Лог модерации GroupReport — для начисления админу 15₽ за каждое action."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0065_group_reports'),
    ]

    operations = [
        migrations.CreateModel(
            name='GroupReportReviewLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('action', models.CharField(
                    choices=[
                        ('approved', 'Одобрено'),
                        ('rejected', 'Отклонено'),
                        ('rework', 'На доработку'),
                    ],
                    max_length=20,
                )),
                ('admin', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='group_report_review_logs',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('report', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='review_logs',
                    to='core.groupreport',
                )),
            ],
            options={
                'verbose_name': 'Событие модерации GroupReport',
                'verbose_name_plural': 'События модерации GroupReport',
            },
        ),
    ]
