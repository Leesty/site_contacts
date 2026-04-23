#!/bin/bash
# Идемпотентный патч upsert_telegram_user — защита от race condition
# при параллельных /start от одного telegram_id. Без патча — IntegrityError
# на UNIQUE constraint "telegram_users_telegram_id_key" крашит хендлер
# `on_start`, и клиент видит "кнопку Старт не жмётся" (welcome сообщение
# не отправляется). SearchLink webhook к этому моменту уже ушёл, статистика
# корректна, но UX ломается.
#
# Фикс: вставка в savepoint (db.begin_nested()); на IntegrityError — rollback
# только savepoint-а, не всей сессии, и перечитываем существующую запись.

FILE=/opt/windowgram/backend/app/services/telegram_user_service.py
LOG=/var/log/searchlink_patch.log

[ ! -f "$FILE" ] && echo "$(date): UPSERT PATCH — FILE NOT FOUND" >> $LOG && exit 0
grep -q "upsert_telegram_user: race-safe savepoint" "$FILE" && exit 0

echo "$(date): UPSERT PATCH MISSING — applying..." >> $LOG

python3 << 'PYEOF'
FILE = "/opt/windowgram/backend/app/services/telegram_user_service.py"
with open(FILE) as f:
    c = f.read()

if "upsert_telegram_user: race-safe savepoint" in c:
    exit(0)

OLD = '''async def upsert_telegram_user(db: AsyncSession, tg_user: TgUser) -> TelegramUser:
    result = await db.execute(
        select(TelegramUser).where(TelegramUser.telegram_id == tg_user.id)
    )
    user = result.scalar_one_or_none()

    if user:
        user.first_name = tg_user.first_name
        user.last_name = tg_user.last_name
        user.username = tg_user.username
    else:
        user = TelegramUser(
            platform="telegram",
            telegram_id=tg_user.id,
            first_name=tg_user.first_name,
            last_name=tg_user.last_name,
            username=tg_user.username,
        )
        db.add(user)
        await db.flush()

    return user'''

NEW = '''async def upsert_telegram_user(db: AsyncSession, tg_user: TgUser) -> TelegramUser:
    # upsert_telegram_user: race-safe savepoint — если два /start приходят параллельно
    # от одного telegram_id, второй ловит IntegrityError на UNIQUE-constraint. Используем
    # SAVEPOINT чтобы откатить только попытку INSERT, не всю сессию, и перечитать
    # существующую запись.
    from sqlalchemy.exc import IntegrityError as _UpsertIntegrityError
    result = await db.execute(
        select(TelegramUser).where(TelegramUser.telegram_id == tg_user.id)
    )
    user = result.scalar_one_or_none()

    if user:
        user.first_name = tg_user.first_name
        user.last_name = tg_user.last_name
        user.username = tg_user.username
        return user

    try:
        async with db.begin_nested():
            user = TelegramUser(
                platform="telegram",
                telegram_id=tg_user.id,
                first_name=tg_user.first_name,
                last_name=tg_user.last_name,
                username=tg_user.username,
            )
            db.add(user)
            await db.flush()
    except _UpsertIntegrityError:
        result = await db.execute(
            select(TelegramUser).where(TelegramUser.telegram_id == tg_user.id)
        )
        user = result.scalar_one()
        user.first_name = tg_user.first_name
        user.last_name = tg_user.last_name
        user.username = tg_user.username

    return user'''

if OLD not in c:
    print("PATTERN NOT FOUND")
    exit(1)

c = c.replace(OLD, NEW, 1)
with open(FILE, "w") as f:
    f.write(c)

import py_compile
try:
    py_compile.compile(FILE, doraise=True)
    print("PATCHED")
except py_compile.PyCompileError as e:
    print(f"SYNTAX ERROR: {e}")
    exit(1)
PYEOF

if grep -q "upsert_telegram_user: race-safe savepoint" "$FILE"; then
    echo "$(date): UPSERT PATCH APPLIED OK" >> $LOG
else
    echo "$(date): UPSERT PATCH FAILED" >> $LOG
    exit 1
fi
