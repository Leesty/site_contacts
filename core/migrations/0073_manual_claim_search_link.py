"""ManualSearchClaim теперь относится к конкретной SearchLink менеджера —
кнопка появилась прямо в карточке ссылки в /search/links/."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0072_manual_claim_pending_review'),
    ]

    operations = [
        migrations.AddField(
            model_name='manualsearchclaim',
            name='search_link',
            field=models.OneToOneField(
                blank=True, null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='manual_claim', to='core.searchlink',
                help_text='SearchLink менеджера, к которой относится ручная привязка.',
            ),
        ),
    ]
