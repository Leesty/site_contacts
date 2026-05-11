"""Ручные SearchLink-привязки (menager submits client_id manually, 150 ₽
if not already claimed by anyone)."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0070_group_report_referrals'),
    ]

    operations = [
        migrations.CreateModel(
            name='ManualSearchClaim',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('raw_input', models.CharField(
                    max_length=255,
                    help_text='То, что менеджер ввёл (telegram_id, @username, vk.com/...).',
                )),
                ('normalized_identifier', models.CharField(
                    max_length=255, db_index=True,
                    help_text='Нормализованный идентификатор (telegram:user, vk:idNNN и т.п.) — для дедупа.',
                )),
                ('platform', models.CharField(
                    max_length=16, default='other',
                    choices=[('telegram', 'Telegram'), ('vk', 'VK'), ('other', 'Other')],
                )),
                ('telegram_id', models.BigIntegerField(blank=True, db_index=True, null=True)),
                ('telegram_username', models.CharField(blank=True, db_index=True, default='', max_length=100)),
                ('vk_user_id', models.BigIntegerField(blank=True, db_index=True, null=True)),
                ('vk_screen_name', models.CharField(blank=True, db_index=True, default='', max_length=100)),
                ('status', models.CharField(
                    max_length=20, db_index=True,
                    choices=[('approved', 'Одобрено'), ('rejected', 'Отклонено')],
                )),
                ('rejection_reason', models.TextField(blank=True, default='')),
                ('paid_reward', models.PositiveIntegerField(
                    default=0,
                    help_text='Сколько начислено менеджеру в момент сабмита (150 при approved, 0 при rejected).',
                )),
                ('matched_search_link', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='manual_claims_conflicting',
                    to='core.searchlink',
                    help_text='Существующая SearchLink с этим клиентом, из-за которой claim отклонён.',
                )),
                ('matched_manual_claim', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='conflicts', to='core.manualsearchclaim',
                    help_text='Предыдущий ManualSearchClaim с тем же клиентом.',
                )),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='manual_search_claims',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Ручная привязка клиента (SearchLink)',
                'verbose_name_plural': 'Ручные привязки клиентов (SearchLink)',
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['user', 'status'], name='core_manual_user_id_idx'),
                    models.Index(fields=['normalized_identifier', 'status'], name='core_manual_norm_idx'),
                ],
            },
        ),
    ]
