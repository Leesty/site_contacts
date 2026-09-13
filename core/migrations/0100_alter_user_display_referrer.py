import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Только текст подсказки поля — SQL не порождает."""

    dependencies = [("core", "0099_user_display_referrer")]

    operations = [
        migrations.AlterField(
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
                    "человека, НЕ меняя получателя денег: реф-начисления всегда идут "
                    "partner_owner."
                ),
            ),
        ),
    ]
