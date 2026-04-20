#!/bin/bash
# Идемпотентный патч VK-бота для поддержки SearchLink ref-параметра.
# Кладётся на 72.56.24.65 в /opt/windowgram/ и добавляется в
# /etc/systemd/system/windowgram.service.d/vk_searchlink.conf как ExecStartPre.
#
# КРИТИЧНО: ref-чек ставится в САМОЕ НАЧАЛО on_vk_message handler,
# ДО любых return/фильтров (включая skip-empty-text). Иначе клик
# кнопки «Начать» (payload без текста) будет отфильтрован и ref
# потеряется, даже если VK его прислал.
#
# Маркер идемпотентности — уникальная строка "SearchLink VK: ловим ref ДО"
# (а не просто "_vk_sl_ref", который может остаться от старой версии
# битого патча в середине handler'а — см. обработку ниже).

HANDLERS=/opt/windowgram/backend/app/bot_runner/vk_handlers.py
LOG=/var/log/searchlink_vk_patch.log

[ ! -f "$HANDLERS" ] && echo "$(date): FILE NOT FOUND" >> $LOG && exit 0
grep -q "SearchLink VK: ловим ref ДО" "$HANDLERS" && exit 0

echo "$(date): VK PATCH MISSING — applying..." >> $LOG

python3 << 'PYEOF'
FILE = "/opt/windowgram/backend/app/bot_runner/vk_handlers.py"
with open(FILE) as f:
    content = f.read()

if "SearchLink VK: ловим ref ДО" in content:
    exit(0)

# 1) Константы webhook (если ещё нет)
if "SEARCHLINK_WEBHOOK_URL" not in content:
    inject_const = """logger = logging.getLogger(__name__)

# SearchLink webhook (VK)
SEARCHLINK_WEBHOOK_URL = "https://rupartnerka.ru/api/search-bot-start/"
SEARCHLINK_WEBHOOK_SECRET = "p9EMWO1uPz75wFTEh2JS0Vo2oYtdAeyOs0veeH9FVu8"
"""
    content = content.replace("logger = logging.getLogger(__name__)", inject_const)

# 2) Удаляем старые/битые версии патча (если были вставлены в середине handler).
import re
for old_pattern in [
    r'\s+# SearchLink VK: если клиент пришёл.*?logger\.warning\("SearchLink VK webhook failed: %s", _e\)\s*\n',
    r'\s+_vk_sl_ref = getattr\(message, "ref", None\) or ""\s*\n\s+logger\.info\("SearchLink VK debug:.*?\n',
]:
    content = re.sub(old_pattern, "\n        ", content, count=1, flags=re.DOTALL)

# 3) Вставляем рабочий блок в САМОЕ НАЧАЛО on_vk_message handler
marker = "async def on_vk_message(message: VkMessage):\n        peer_id = message.peer_id"
inject = '''async def on_vk_message(message: VkMessage):
        peer_id = message.peer_id

        # SearchLink VK: ловим ref ДО любых фильтров. VK шлёт ref только при первом
        # контакте нового пользователя с сообществом. Debug-лог фиксирует каждое
        # входящее сообщение, чтобы видеть что именно прилетает от VK.
        _vk_sl_ref = getattr(message, "ref", None) or ""
        _vk_sl_ref_src = getattr(message, "ref_source", None) or ""
        _vk_sl_payload = getattr(message, "payload", None) or ""
        logger.info("SearchLink VK debug: from_id=%s text=%r ref=%r ref_source=%r payload=%r", message.from_id, (message.text or "")[:40], _vk_sl_ref, _vk_sl_ref_src, _vk_sl_payload[:60] if isinstance(_vk_sl_payload, str) else _vk_sl_payload)
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
                logger.warning("SearchLink VK webhook failed: %s", _e)'''

if marker in content:
    content = content.replace(marker, inject, 1)
    with open(FILE, "w") as f:
        f.write(content)
    import py_compile
    try:
        py_compile.compile(FILE, doraise=True)
        print("PATCHED")
    except py_compile.PyCompileError as e:
        print(f"SYNTAX ERROR after patch: {e}")
        exit(1)
else:
    print("PATTERN NOT FOUND — manual check needed")
    exit(1)
PYEOF

if grep -q "SearchLink VK: ловим ref ДО" "$HANDLERS"; then
    echo "$(date): VK PATCH APPLIED OK" >> $LOG
else
    echo "$(date): VK PATCH FAILED" >> $LOG
    exit 1
fi
