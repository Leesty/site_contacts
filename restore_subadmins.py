"""Восстановление подадминства менеджеров на windowgram после паузы 30.07.2026.

Запуск:
    python manage.py shell < restore_subadmins.py

Возвращает роль `sub` на бот-сервере всем, у кого на сайте стоит
`can_create_group_reports=True` (61 чел., права и привязки не снимались).
Идемпотентно: повторный запуск ничего не сломает.

Не забыть также вернуть создание чатов из кабинета «Прозвоны»:
    COLD_CHAT_CREATION_ENABLED=true  (base_site/settings.py)
"""
import time

from core.models import User
from core.views_group_reports import _windowgram_register_subadmin

ok = fail = 0
errors = []
for u in User.objects.filter(can_create_group_reports=True).order_by("username"):
    tg = (u.bot_admin_tg_username or "").strip()
    vk = (u.bot_admin_vk_screen_name or "").strip()
    for platform, uname in (("telegram", tg), ("vk", vk)):
        if not uname:
            continue
        good, note = _windowgram_register_subadmin(
            platform=platform, platform_user_id=None,
            username=uname, display_name=u.username,
        )
        if good:
            ok += 1
        else:
            fail += 1
            errors.append(f"@{u.username} ({platform}/{uname}): {note}")
        time.sleep(0.15)

print("восстановлено привязок:", ok, "| ошибок:", fail)
for e in errors[:15]:
    print("  ERR", e)
