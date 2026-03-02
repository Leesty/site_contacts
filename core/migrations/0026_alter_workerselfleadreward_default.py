from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0025_worker_self_lead"),
    ]

    operations = [
        migrations.AlterField(
            model_name="workerselflead",
            name="reward",
            field=models.PositiveIntegerField(default=50, help_text="Вознаграждение за одобренный лид (руб.)."),
        ),
    ]
