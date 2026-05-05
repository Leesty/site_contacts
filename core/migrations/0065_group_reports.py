"""Group reports (бета): право + сама модель.

Намеренно оставлены только GroupReport-связанные операции. В автогенерации
makemigrations подтянул побочные AlterField/RemoveField чужих моделей —
их я не трогаю, это отдельная зона ответственности.
"""

import core.models
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0064_searchreport_review_log'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='can_create_group_reports',
            field=models.BooleanField(
                db_index=True,
                default=False,
                help_text='Может ли менеджер создавать отчёты по группам (отдельный поток отчётов).',
            ),
        ),
        migrations.CreateModel(
            name='GroupReport',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('platform', models.CharField(
                    choices=[('telegram', 'Telegram'), ('vk', 'VK')],
                    default='telegram',
                    help_text='Платформа группы (TG/VK).',
                    max_length=16,
                )),
                ('client_platform_id', models.BigIntegerField(
                    blank=True, db_index=True,
                    help_text='telegram_id или vk_id клиента (для авто-валидации).',
                    null=True,
                )),
                ('client_username', models.CharField(
                    blank=True, max_length=100,
                    help_text='@username (TG) или screen_name (VK), либо ссылка vk.com/...',
                )),
                ('manager_platform_id', models.BigIntegerField(
                    blank=True, db_index=True,
                    help_text='telegram_id или vk_id самого менеджера в боте.',
                    null=True,
                )),
                ('manager_username', models.CharField(
                    blank=True, max_length=100,
                    help_text='@username/screen_name менеджера.',
                )),
                ('report_date', models.DateField(
                    default=django.utils.timezone.now,
                    help_text='Дата отчёта (когда был совершён созвон/работа).',
                )),
                ('screencast', models.FileField(
                    blank=True, null=True,
                    help_text='Скринкаст переписки до создания чата (видео).',
                    upload_to=core.models.group_report_upload_to,
                )),
                ('status', models.CharField(
                    choices=[
                        ('pending', 'На проверке'),
                        ('approved', 'Одобрен'),
                        ('rejected', 'Отклонён'),
                        ('rework', 'На доработке'),
                    ],
                    db_index=True, default='pending', max_length=20,
                )),
                ('is_complete', models.BooleanField(
                    db_index=True, default=False,
                    help_text=('Все 4 этапа в admin_task_progress на бот-сервере выполнены: '
                               'Артём приглашён, /линк, /оффер, /созвон. Без этого отчёт '
                               'виден только главному админу.'),
                )),
                ('validation_note', models.TextField(
                    blank=True,
                    help_text='Что не хватило для is_complete (artem/link/offer/sozvon).',
                )),
                ('rejection_reason', models.TextField(blank=True)),
                ('rework_comment', models.TextField(blank=True)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('paid_reward', models.IntegerField(
                    default=0,
                    help_text='Сколько фактически начислено при approve (для аудита).',
                )),
                ('reviewed_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='reviewed_group_reports',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('user', models.ForeignKey(
                    help_text='Менеджер, отправивший отчёт.',
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='group_reports',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Отчёт по группе',
                'verbose_name_plural': 'Отчёты по группам',
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['status', 'is_complete'], name='core_groupr_status_c8e8e6_idx'),
                    models.Index(fields=['user', 'status'], name='core_groupr_user_id_69b6b8_idx'),
                ],
            },
        ),
    ]
