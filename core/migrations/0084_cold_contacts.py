# Cold contacts (списки контактов менеджера) — модели для воронки 3 попыток дозвона.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0083_lid_limits_and_pool'),
    ]

    operations = [
        migrations.CreateModel(
            name='ColdContact',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('source', models.CharField(blank=True, help_text='Источник контакта.', max_length=255)),
                ('contact', models.CharField(help_text='Номер телефона / контакт.', max_length=255)),
                ('name', models.CharField(blank=True, help_text='Имя клиента (если стал лидом).', max_length=255)),
                ('final_status', models.CharField(
                    choices=[
                        ('in_progress', 'В работе'),
                        ('lead', 'Лид'),
                        ('refused', 'Отказ'),
                        ('no_answer', 'Нет ответа (3 НДЗ)'),
                    ],
                    db_index=True, default='in_progress', max_length=20,
                )),
                ('lead_call_date', models.DateField(blank=True, help_text='Дата созвона (при статусе «лид»).', null=True)),
                ('lead_call_time', models.TimeField(blank=True, help_text='Время созвона МСК (при статусе «лид»).', null=True)),
                ('owner', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='cold_contacts',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Холодный контакт',
                'verbose_name_plural': 'Холодные контакты',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='CallAttempt',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('attempt_no', models.PositiveSmallIntegerField(help_text='Номер попытки: 1 / 2 / 3.')),
                ('status', models.CharField(
                    choices=[
                        ('answered', 'Дозвонился'),
                        ('ndz', 'Недозвон (НДЗ)'),
                        ('lead', 'Лид'),
                        ('callback', 'Перезвонить'),
                        ('refused', 'Отказ'),
                    ],
                    max_length=20,
                )),
                ('callback_at', models.DateTimeField(blank=True, help_text='Когда перезвонить (только при статусе callback).', null=True)),
                ('note', models.TextField(blank=True)),
                ('contact', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='attempts',
                    to='core.coldcontact',
                )),
            ],
            options={
                'verbose_name': 'Попытка дозвона',
                'verbose_name_plural': 'Попытки дозвона',
                'ordering': ['contact_id', 'attempt_no'],
            },
        ),
        migrations.AddIndex(
            model_name='coldcontact',
            index=models.Index(fields=['owner', 'final_status'], name='core_coldco_owner_i_a3a33c_idx'),
        ),
        migrations.AddIndex(
            model_name='coldcontact',
            index=models.Index(fields=['owner', '-created_at'], name='core_coldco_owner_i_12760c_idx'),
        ),
        migrations.AddConstraint(
            model_name='callattempt',
            constraint=models.UniqueConstraint(fields=('contact', 'attempt_no'), name='uniq_attempt_per_contact_no'),
        ),
    ]
