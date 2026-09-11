from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("core", "0097_booking_seen_at")]

    operations = [
        migrations.AddField(
            model_name="searchlink",
            name="clicked_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                db_index=True,
                help_text=(
                    "Когда лендинг открыл ЖИВОЙ человек. Ставится только из браузера "
                    "(JS-пинг на /s/<code>/hit/), роботы скрипты не выполняют. "
                    "NULL = живых переходов не было."
                ),
            ),
        ),
        migrations.AlterField(
            model_name="searchlink",
            name="visitor_ip",
            field=models.GenericIPAddressField(
                blank=True,
                null=True,
                help_text=(
                    "IP посетителя при открытии лендинга. ВНИМАНИЕ: сюда попадают и "
                    "роботы — Яндекс, Google, превьюшники Telegram/VK дёргают страницу "
                    "сразу после того, как менеджер вставил ссылку в чат. Как признак "
                    "«человек перешёл» НЕ годится, для этого есть clicked_at."
                ),
            ),
        ),
    ]
