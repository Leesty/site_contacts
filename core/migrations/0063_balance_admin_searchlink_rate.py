"""User.balance_admin_searchlink_rate — ставка баланс-админа за SearchLink (default 15 ₽)."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0062_searchreport_normalized_contact"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="balance_admin_searchlink_rate",
            field=models.DecimalField(
                max_digits=6, decimal_places=2, default=15,
                help_text="Ставка баланс-админа за каждый одобренный SearchLink-отчёт от не-реферала.",
            ),
        ),
    ]
