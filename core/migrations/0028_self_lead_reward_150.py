from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0027_partner_system"),
    ]

    operations = [
        migrations.AlterField(
            model_name="workerselflead",
            name="reward",
            field=models.PositiveIntegerField(
                default=150,
                help_text="Вознаграждение за одобренный лид (руб.).",
            ),
        ),
    ]
