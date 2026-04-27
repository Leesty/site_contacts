"""Переход на pull-модель: убираем поля исходящих звонков, добавляем поллинг входящих."""
from django.db import migrations, models


def set_incoming_campaign(apps, schema_editor):
    SiteSettings = apps.get_model("core", "SiteSettings")
    for s in SiteSettings.objects.all():
        if not s.zvonok_incoming_campaign_id:
            s.zvonok_incoming_campaign_id = "1738255164"
            s.save(update_fields=["zvonok_incoming_campaign_id"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0056_withdrawalreceipt"),
    ]

    operations = [
        # SiteSettings: добавляем входящую кампанию, удаляем три исходящих + тестовое поле
        migrations.AddField(
            model_name="sitesettings",
            name="zvonok_incoming_campaign_id",
            field=models.CharField(
                max_length=64, blank=True, default="1738255164",
                help_text="ID входящей кампании zvonok.com — где приходят звонки от клиентов на наши номера.",
            ),
        ),
        migrations.RunPython(set_incoming_campaign, migrations.RunPython.noop),
        # NB: старые поля zvonok_campaign_id_now / _1h / _10min оставлены в БД
        # как осиротевшие колонки. Drop отдельной миграцией позже, чтобы избежать
        # downtime при автодеплое App Platform.

        # SearchReport: поллинг входящих звонков
        migrations.AddField(
            model_name="searchreport",
            name="zvonok_last_polled_at",
            field=models.DateTimeField(
                null=True, blank=True, db_index=True,
                help_text="Когда отчёт последний раз опрашивался в zvonok API.",
            ),
        ),
        migrations.AddField(
            model_name="searchreport",
            name="zvonok_call_id",
            field=models.CharField(
                max_length=64, blank=True, default="",
                help_text="call_id от zvonok — какой звонок подтвердил отчёт (для аудита).",
            ),
        ),
        migrations.AlterField(
            model_name="searchreport",
            name="callback_at",
            field=models.DateTimeField(
                null=True, blank=True,
                help_text="Устаревшее поле от старой системы исходящих звонков (не используется).",
            ),
        ),
        migrations.AlterField(
            model_name="searchreport",
            name="callback_confirmed_at",
            field=models.DateTimeField(
                null=True, blank=True,
                help_text="Когда поллер подтвердил входящий звонок клиента с нажатой «1».",
            ),
        ),
        migrations.AlterField(
            model_name="sitesettings",
            name="zvonok_webhook_secret",
            field=models.CharField(
                max_length=64, blank=True, default="",
                help_text="Секрет для cron-эндпоинта поллинга входящих звонков (в URL ?secret=...).",
            ),
        ),
    ]
