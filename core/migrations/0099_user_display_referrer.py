import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("core", "0098_searchlink_clicked_at")]

    operations = [
        migrations.AddField(
            model_name="user",
            name="display_referrer",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="display_referrals",
                limit_choices_to={"role__in": ["partner", "user"]},
                to=settings.AUTH_USER_MODEL,
                help_text=(
                    "Показывать этого пользователя в списке рефералов у указанного "
                    "человека, НЕ меняя получателя денег. Все реф-начисления по-прежнему "
                    "идут partner_owner. Пример (13.09.2026): viktorseverin0209 закреплена "
                    "за @Nastia051189, а 50/1000 ₽ продолжают идти @Nastya_Partner."
                ),
            ),
        ),
    ]
