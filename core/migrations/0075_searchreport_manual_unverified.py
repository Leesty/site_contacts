"""SearchReport.manual_unverified — для отчётов с ручной привязкой
клиента, которого нет в БД бота (написал руководителю в ЛС, минуя бота).
Идут на отдельную модерацию главному админу."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0074_user_ref_cuts'),
    ]

    operations = [
        migrations.AddField(
            model_name='searchreport',
            name='manual_unverified',
            field=models.BooleanField(
                db_index=True, default=False,
                help_text=('Менеджер вписал ID клиента вручную, но клиента нет в БД '
                           'бота (не запускал бота, написал в ЛС напрямую). Такие '
                           'отчёты идут на отдельную модерацию главному админу.'),
            ),
        ),
    ]
