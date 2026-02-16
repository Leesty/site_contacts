"""Вспомогательные функции для лидов: автоопределение типа базы, сжатие скриншотов, нормализация контактов."""
from __future__ import annotations

import io
import os
import re
from typing import TYPE_CHECKING

from django.db.models import Q

if TYPE_CHECKING:
    from .models import User

from .models import BaseType, Contact


def normalize_lead_contact(contact: str) -> str:
    """Комплексная нормализация контакта для проверки дубликатов по всей базе.

    @lestily = t.me/lestily = telegram:lestily; vk.com/id1 = vk.ru/id1 = vk:id1;
    ссылки без протокола и www, номера 8... -> 7...
    """
    if not contact or not contact.strip():
        return ""
    c = contact.strip().lower()
    c = c.replace("https://", "").replace("http://", "").replace("www.", "").strip()
    # Спец. домены (Юла и т.д.)
    if "mail.ru" in c or "youla.ru" in c:
        return c
    # Telegram: @user, t.me/user, telegram.me/user -> telegram:user
    for prefix in ("t.me/", "telegram.me/", "telegram.dog/"):
        if prefix in c:
            idx = c.find(prefix)
            rest = c[idx + len(prefix) :].split("?")[0].strip().rstrip("/")
            if rest:
                return "telegram:" + rest
    if c.startswith("@"):
        rest = c[1:].split("?")[0].strip().rstrip("/")
        if rest:
            return "telegram:" + rest
    # VK: vk.com/xxx и vk.ru/xxx -> vk:xxx
    for domain in ("vk.com/", "vk.ru/"):
        if domain in c:
            idx = c.find(domain)
            rest = c[idx + len(domain) :].split("?")[0].strip().rstrip("/")
            if rest:
                return "vk:" + rest
    # Instagram
    if "instagram.com/" in c:
        idx = c.find("instagram.com/")
        rest = c[idx + 14 :].split("?")[0].strip().rstrip("/")
        if rest:
            return "ig:" + rest
    # OK
    if "ok.ru/" in c:
        idx = c.find("ok.ru/")
        rest = c[idx + 6 :].split("?")[0].strip().rstrip("/")
        if rest:
            return "ok:" + rest
    # Avito
    if "avito.ru/" in c:
        idx = c.find("avito.ru/")
        rest = c[idx + 9 :].split("?")[0].strip().rstrip("/")
        if rest:
            return "avito:" + rest
    # Номера: только цифры, 8XXXXXXXXXX -> 7XXXXXXXXXX
    c_digits = re.sub(r"[\s\-\(\)\+]", "", c)
    if c_digits.isdigit():
        if c_digits.startswith("8") and len(c_digits) == 11:
            c_digits = "7" + c_digits[1:]
        return "phone:" + c_digits
    # Один «словесный» логин без ссылки (lestily, user_name) — считаем Telegram
    if re.match(r"^[a-z0-9_]{2,}$", c):
        return "telegram:" + c
    return c


def raw_contact_to_url(contact: str) -> str | None:
    """По значению контакта возвращает URL для перехода (Telegram, VK, WhatsApp, Instagram и т.д.) или None."""
    if not contact or not contact.strip():
        return None
    normalized = normalize_lead_contact(contact)
    if not normalized:
        return None
    if normalized.startswith("telegram:"):
        username = normalized[9:].strip()
        if username:
            return f"https://t.me/{username}"
    if normalized.startswith("vk:"):
        path = normalized[3:].strip()
        if path:
            return f"https://vk.com/{path}"
    if normalized.startswith("ig:"):
        username = normalized[3:].strip()
        if username:
            return f"https://instagram.com/{username}"
    if normalized.startswith("ok:"):
        path = normalized[3:].strip()
        if path:
            return f"https://ok.ru/{path}"
    if normalized.startswith("avito:"):
        path = normalized[6:].strip()
        if path:
            return f"https://avito.ru/{path}"
    if normalized.startswith("phone:"):
        digits = normalized[6:].strip().replace(" ", "")
        if digits and digits.isdigit():
            return f"https://wa.me/{digits}"
    return None


def determine_base_type_for_contact(raw_contact: str, user: User) -> BaseType | None:
    """Определяет тип базы по контакту: по URL или по наличию в выданных/общих базах (как в боте)."""
    if not raw_contact or not raw_contact.strip():
        return None
    contact_lower = raw_contact.strip().lower()

    # По URL — только те типы, которые есть в BaseType
    if "instagram.com" in contact_lower:
        return BaseType.objects.filter(slug="instagram").first()
    if "vk.com" in contact_lower or "vk.ru" in contact_lower:
        return BaseType.objects.filter(slug="vk").first()
    if "ok.ru" in contact_lower:
        return BaseType.objects.filter(slug="ok").first()
    if "t.me" in contact_lower or contact_lower.startswith("@"):
        return BaseType.objects.filter(slug="telegram").first()
    # avito, yula, kwork — нет в BaseType, не подставляем

    # По базе контактов: сначала выданные пользователю, потом вся база
    value_clean = raw_contact.strip()
    contact = (
        Contact.objects.filter(
            Q(value__iexact=value_clean) | Q(value=value_clean),
            assigned_to=user,
        )
        .select_related("base_type")
        .first()
    )
    if contact:
        return contact.base_type
    contact = (
        Contact.objects.filter(
            Q(value__iexact=value_clean) | Q(value=value_clean),
        )
        .select_related("base_type")
        .first()
    )
    if contact:
        return contact.base_type
    return None


def compress_lead_attachment(lead) -> bool:
    """Сжимает файл вложения лида, если это изображение. Перезаписывает файл на месте.
    При хранении в S3 (нет локального path) сжатие не выполняется."""
    if not lead or not getattr(lead, "attachment", None) or not lead.attachment:
        return False
    try:
        from PIL import Image
    except ImportError:
        return False

    # Для S3-хранилищ (django-storages S3Storage) у файлов нет локального пути,
    # свойство .path либо отсутствует, либо выбрасывает NotImplementedError.
    # В этом случае просто пропускаем сжатие: файл уже лежит в бакете.
    try:
        path = lead.attachment.path  # type: ignore[assignment]
    except Exception:
        return False
    if not path or not os.path.exists(path):
        return False
    try:
        with Image.open(path) as img:
            if img.format not in ("JPEG", "PNG", "GIF", "WEBP"):
                return False
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            elif img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            max_side = 1600
            w, h = img.size
            if w > max_side or h > max_side:
                if w >= h:
                    new_w, new_h = max_side, int(h * max_side / w)
                else:
                    new_w, new_h = int(w * max_side / h), max_side
                img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=82, optimize=True)
            buf.seek(0)
        with open(path, "wb") as f:
            f.write(buf.getvalue())
        return True
    except Exception:
        return False
