"""SearchLink: поле duplicate_of (FK на оригинальную ссылку, если эта дубликат)."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0060_partner_searchlink_dozhim_cuts"),
    ]

    operations = [
        migrations.AddField(
            model_name="searchlink",
            name="duplicate_of",
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=models.SET_NULL,
                related_name="duplicate_links",
                to="core.searchlink",
                db_index=True,
                help_text="Если этот клиент уже привлекался по другой SearchLink — здесь FK на оригинал. Дубликаты автоодобрению не подлежат.",
            ),
        ),
    ]
