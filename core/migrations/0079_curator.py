"""Curator: внешние тимлиды-кураторы.

Главный админ заводит TG-ник куратора и привязывает к нему юзеров сайта.
Дальше планируется работа с кураторами — пока хранилище + список юзеров.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0078_subref_milestone'),
    ]

    operations = [
        migrations.CreateModel(
            name='Curator',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('tg_username', models.CharField(
                    db_index=True,
                    help_text='@ник в Telegram (без @, lower-case). Уникальный.',
                    max_length=100, unique=True,
                )),
                ('display_name', models.CharField(
                    blank=True, default='',
                    help_text='Человеческое имя (необязательно — для удобства).',
                    max_length=255,
                )),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('created_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='created_curators',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Куратор',
                'verbose_name_plural': 'Кураторы',
                'ordering': ['tg_username'],
            },
        ),
        migrations.AddField(
            model_name='user',
            name='curator',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='users',
                to='core.curator',
                help_text='Куратор-тимлид, который ведёт этого пользователя в TG.',
            ),
        ),
    ]
