from django.db import migrations, models
import django.db.models.deletion


def backfill_invited_by(apps, schema_editor):
    """Для существующих рефералов пригласивший = текущий рефовод.

    До этой миграции обе роли совмещал partner_owner, поэтому исторически
    «пригласил» и «получает %» — один и тот же человек.
    """
    User = apps.get_model("core", "User")
    User.objects.filter(partner_owner__isnull=False, invited_by__isnull=True).update(
        invited_by=models.F("partner_owner")
    )


class Migration(migrations.Migration):
    """Разделение «кто пригласил» (invited_by, для milestone) и «кто получает %»
    (partner_owner). Только AddField + backfill существующих связей."""

    dependencies = [
        ('core', '0092_chat_credited_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='invited_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='invited_users',
                to='core.user',
                help_text=(
                    'Кто ФАКТИЧЕСКИ пригласил (по чьей ссылке зарегистрировался) — для milestone-бонуса. '
                    'Может отличаться от partner_owner: если пригласивший неаккредитован, он получает '
                    'только 500 ₽ за 10 клиентов, а % идёт выше по цепочке аккредитованному.'
                ),
            ),
        ),
        migrations.RunPython(backfill_invited_by, migrations.RunPython.noop),
    ]
