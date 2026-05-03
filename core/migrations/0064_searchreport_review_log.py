"""SearchReportReviewLog — фиксирует каждое action админа над SR-отчётом.

Раньше earnings админа за SR считались по reviewed_by + status. Если другой
админ менял статус, первый терял кредит. Теперь — лог, как у LeadReviewLog.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0063_balance_admin_searchlink_rate"),
    ]

    operations = [
        migrations.CreateModel(
            name="SearchReportReviewLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("action", models.CharField(
                    max_length=20,
                    choices=[("approved", "Одобрено"), ("rejected", "Отклонено"), ("rework", "На доработку")],
                )),
                ("admin", models.ForeignKey(
                    null=True, blank=True,
                    on_delete=models.SET_NULL,
                    related_name="search_report_review_logs",
                    to="core.user",
                )),
                ("report", models.ForeignKey(
                    on_delete=models.CASCADE,
                    related_name="review_logs",
                    to="core.searchreport",
                )),
            ],
            options={
                "verbose_name": "Событие модерации SearchLink-отчёта",
                "verbose_name_plural": "События модерации SearchLink-отчётов",
            },
        ),
    ]
