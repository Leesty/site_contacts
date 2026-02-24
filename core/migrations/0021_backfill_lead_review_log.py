from django.db import migrations


def backfill_lead_review_log(apps, schema_editor):
    """
    Заполнить LeadReviewLog для уже существующих лидов по их текущему финальному статусу.

    Раньше статистика по админам строилась по полям Lead.reviewed_by/status.
    После перехода на LeadReviewLog старая статистика "обнулилась", потому что лог был пустой.

    Здесь мы один раз создаём по ОДНОМУ событию для каждого лида, у которого уже есть reviewed_by:
    - status=approved  -> action=approved
    - status=rejected  -> action=rejected
    - status=rework    -> action=rework

    Исторические промежуточные действия (многократные доработки и т.п.) восстановить нельзя,
    но итоговая статистика по "кто что проверил" станет такой же, как была раньше.
    """
    Lead = apps.get_model("core", "Lead")
    LeadReviewLog = apps.get_model("core", "LeadReviewLog")

    batch = []
    for lead in Lead.objects.filter(reviewed_by__isnull=False).only(
        "id", "status", "reviewed_by_id", "created_at"
    ):
        if lead.status == "approved":
            action = "approved"
        elif lead.status == "rejected":
            action = "rejected"
        elif lead.status == "rework":
            action = "rework"
        else:
            continue
        batch.append(
            LeadReviewLog(
                lead_id=lead.id,
                admin_id=lead.reviewed_by_id,
                action=action,
                created_at=getattr(lead, "reviewed_at", None) or lead.created_at,
                updated_at=getattr(lead, "reviewed_at", None) or lead.created_at,
            )
        )
    if batch:
        LeadReviewLog.objects.bulk_create(batch, ignore_conflicts=True)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0020_add_lead_review_log"),
    ]

    operations = [
        migrations.RunPython(backfill_lead_review_log, reverse_code=noop),
    ]

