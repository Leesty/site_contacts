from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0035_affiliate_role_and_ref_reward"),
    ]

    operations = [
        migrations.AlterField(
            model_name="workerselflead",
            name="reward",
            field=models.PositiveIntegerField(default=40, help_text="Вознаграждение за одобренный лид (руб.)."),
        ),
    ]
