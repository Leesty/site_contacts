#!/bin/bash
# Идемпотентный патч VK-бота для поддержки SearchLink ref-параметра.
# Кладётся на сервере 72.56.24.65 в /opt/windowgram/ и добавляется в
# /etc/systemd/system/windowgram.service.d/searchlink.conf как ExecStartPre.
#
# При первом сообщении VK-бот читает message.ref (VK прокидывает его, когда
# клиент кликнул по ссылке vk.me/<community>?ref=CODE и написал боту впервые).
# Если ref совпадает с SearchLink кодом — шлёт тот же webhook на Django,
# что и telegram-версия, но с platform=vk.

HANDLERS=/opt/windowgram/backend/app/bot_runner/vk_handlers.py
LOG=/var/log/searchlink_vk_patch.log

[ ! -f "$HANDLERS" ] && echo "$(date): FILE NOT FOUND" >> $LOG && exit 0
grep -q "_vk_sl_ref" "$HANDLERS" && exit 0

echo "$(date): VK PATCH MISSING — applying..." >> $LOG

python3 << 'PYEOF'
FILE = "/opt/windowgram/backend/app/bot_runner/vk_handlers.py"
with open(FILE) as f:
    content = f.read()

if "_vk_sl_ref" in content:
    exit(0)

# 1) Добавить константы webhook (если ещё нет)
if "SEARCHLINK_WEBHOOK_URL" not in content:
    # Ищем последний import и вставляем после него
    import_marker = "logger = logging.getLogger(__name__)"
    inject = """logger = logging.getLogger(__name__)

# SearchLink webhook (VK)
SEARCHLINK_WEBHOOK_URL = "https://rupartnerka.ru/api/search-bot-start/"
SEARCHLINK_WEBHOOK_SECRET = "p9EMWO1uPz75wFTEh2JS0Vo2oYtdAeyOs0veeH9FVu8"
"""
    content = content.replace(import_marker, inject)

# 2) Вставить блок обработки ref в начало message_new handler.
#    Паттерн: самое начало функции-обработчика. Вставляем ПЕРЕД основным кодом.
#    Ищем "vk_user_id = message.from_id" — это типичный первый шаг в vk_handlers.py
markers = [
    "vk_user_id = message.from_id",
]

patched = False
for marker in markers:
    if marker in content and "_vk_sl_ref" not in content:
        new = """# SearchLink VK: если клиент пришёл по ссылке vk.me/<community>?ref=CODE,
        # VK прокидывает ref только в ПЕРВОМ сообщении от него.
        _vk_sl_ref = getattr(message, "ref", None) or ""
        if _vk_sl_ref and len(_vk_sl_ref) >= 10:
            try:
                import aiohttp as _aiohttp
                _vk_sl_sn = ""
                _vk_sl_fn = ""
                try:
                    _vk_info = await message.ctx_api.users.get(
                        user_ids=[message.from_id],
                        fields=["screen_name"],
                    )
                    if _vk_info:
                        _vk_sl_sn = getattr(_vk_info[0], "screen_name", "") or ""
                        _vk_sl_fn = getattr(_vk_info[0], "first_name", "") or ""
                except Exception:
                    pass
                async with _aiohttp.ClientSession() as _sess:
                    await _sess.post(
                        SEARCHLINK_WEBHOOK_URL,
                        json={
                            "code": _vk_sl_ref,
                            "platform": "vk",
                            "vk_user_id": message.from_id,
                            "vk_screen_name": _vk_sl_sn,
                            "vk_first_name": _vk_sl_fn,
                        },
                        headers={"Authorization": f"Bearer {SEARCHLINK_WEBHOOK_SECRET}"},
                        timeout=_aiohttp.ClientTimeout(total=5),
                    )
                logger.info("SearchLink VK webhook sent: ref=%s vk_id=%s", _vk_sl_ref, message.from_id)
            except Exception as _e:
                logger.warning("SearchLink VK webhook failed: %s", _e)
        """ + marker
        content = content.replace(marker, new, 1)
        patched = True
        break

if not patched:
    print("PATTERN NOT FOUND — manual check needed")
    exit(1)

with open(FILE, "w") as f:
    f.write(content)
print("PATCHED")
PYEOF

if grep -q "_vk_sl_ref" "$HANDLERS"; then
    echo "$(date): VK PATCH APPLIED OK" >> $LOG
else
    echo "$(date): VK PATCH FAILED" >> $LOG
    exit 1
fi
