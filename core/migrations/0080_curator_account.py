"""Куратор — это аккаунт на сайте (1:1), а не «много подопечных».

Удаляем User.curator (была связь user→curator как «подопечный»).
Добавляем Curator.account (OneToOne на User — сам куратор-аккаунт).

На проде 1 куратор без привязок, 0 юзеров с curator_id — миграция
безопасна, данные не теряются.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0079_curator'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='user',
            name='curator',
        ),
        migrations.AddField(
            model_name='curator',
            name='account',
            field=models.OneToOneField(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='curator_profile',
                to=settings.AUTH_USER_MODEL,
                help_text='Аккаунт куратора на сайте (1:1). NULL — пока не привязан.',
            ),
        ),
    ]
