from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("core", "0096_old_balance")]

    operations = [
        migrations.AddField(
            model_name="searchlink",
            name="booking_seen_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text=(
                    "Когда впервые увидели запись клиента на встречу в боте "
                    "(calendar_events). Нужен для статистики: запись физически "
                    "удаляется при отмене/переносе, поэтому «сейчас есть» ≠ «была». "
                    "NULL = записи не было ни разу."
                ),
            ),
        ),
    ]
