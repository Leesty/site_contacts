"""Sub-referrer milestone: 500 ₽ рефоводу когда его реферал сдал 10 отчётов.

Только для рефералов главных рефоводов (role=user, у которых
partner_owner.partner_owner_id IS NULL). Обычные % бонусы для таких
рефералов не работают."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0077_user_ref_bonus_percent'),
    ]

    operations = [
        # Поле ref_bonus_percent из старого revert'нутого 0077 уже нет в модели;
        # на проде столбец остался (миграция применилась раньше). Безопасно его
        # удалить здесь — он deprecated, в коде не используется. Если хочется
        # сохранить — можно закомментировать RemoveField.
        migrations.RemoveField(
            model_name='user',
            name='ref_bonus_percent',
        ),
        migrations.AddField(
            model_name='user',
            name='subref_bonus_paid_at',
            field=models.DateTimeField(
                blank=True, null=True,
                help_text=('Время, когда рефовод-приглашатель получил 500 ₽ за этого '
                           'реферала (после 10-го одобренного отчёта). NULL — ещё не выплачено.'),
            ),
        ),
    ]
