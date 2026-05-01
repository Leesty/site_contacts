"""SearchReport.normalized_contact — для дедупликации по raw_contact."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0061_searchlink_duplicate_of"),
    ]

    operations = [
        migrations.AddField(
            model_name="searchreport",
            name="normalized_contact",
            field=models.CharField(
                max_length=500,
                blank=True,
                default="",
                db_index=True,
                help_text="Нормализованный raw_contact для поиска дубликатов.",
            ),
        ),
    ]
