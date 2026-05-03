from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()

IMAGE_EXTENSIONS = frozenset(("jpg", "jpeg", "png", "gif", "webp", "bmp", "heic"))
VIDEO_EXTENSIONS = frozenset(("mp4", "mov", "webm", "m4v", "3gp"))


@register.filter
def contact_link(value):
    """Оборачивает контакт в кликабельную ссылку (Telegram, VK, WhatsApp, Instagram и т.д.) или возвращает текст."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return ""
    from core.lead_utils import raw_contact_to_url

    text = str(value).strip()
    url = raw_contact_to_url(text)
    if url:
        return mark_safe(
            '<a href="%s" target="_blank" rel="noopener noreferrer" aria-label="Открыть контакт %s (новое окно)">%s</a>'
            % (escape(url), escape(text), escape(text))
        )
    return escape(text)


import re as _re
_PHONE_RE = _re.compile(r"^\s*[\+]?[\d\-\s\(\)]{10,20}\s*$")


@register.filter
def phone_pretty(value):
    """Если value похоже на телефон — возвращает слитный +XXXXXXXXXXX. Иначе исходник.

    «8 (999) 123-45-67» → «+79991234567»
    «9 321 321 52 52» (10 цифр, старт 9) → «+79213215252»
    «9-321-321-52-52» (11 цифр, аномалия) → «+93213215252» (хотя бы слитно)
    """
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if not _PHONE_RE.match(text):
        return text
    digits = _re.sub(r"\D", "", text)
    if not digits or len(digits) < 10:
        return text
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    elif len(digits) == 10 and digits[0] == "9":
        digits = "7" + digits
    return "+" + digits


@register.filter
def contact_with_tg_check(value):
    """Возвращает HTML: контакт + кнопка «🔍 TG» если контакт — телефон.

    Кнопка ведёт на tg://resolve?phone=DIGITS — нативная URL-схема Telegram,
    которая открывает Telegram Desktop/iOS/Android и пытается найти пользователя
    по номеру. (https://t.me/+номер НЕ работает — это формат invite-ссылок групп,
    не поиска по номеру.)
    """
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    pretty = phone_pretty(text)
    is_phone = pretty.startswith("+7") and pretty[1:].isdigit()
    from core.lead_utils import raw_contact_to_url
    if is_phone:
        digits = pretty[1:]  # 79051977490 без +
        return mark_safe(
            f'<span class="d-inline-flex align-items-center gap-1">'
            f'<span class="font-monospace">{escape(pretty)}</span>'
            f'<a href="tg://resolve?phone={escape(digits)}" '
            f'class="badge bg-secondary text-decoration-none" '
            f'title="Открыть в Telegram-клиенте и проверить номер" '
            f'style="font-size:10px;">🔍 TG</a>'
            f'</span>'
        )
    url = raw_contact_to_url(text)
    if url:
        return mark_safe(
            '<a href="%s" target="_blank" rel="noopener noreferrer">%s</a>'
            % (escape(url), escape(text))
        )
    return escape(text)


@register.filter
def support_attachment_is_image(attachment) -> bool:
    """True, если вложение — изображение (по расширению)."""
    if not attachment or not getattr(attachment, "name", None):
        return False
    ext = (attachment.name or "").rsplit(".", 1)[-1].lower()
    return ext in IMAGE_EXTENSIONS


@register.filter
def lead_attachment_is_video(attachment) -> bool:
    """True, если вложение лида — видео (по расширению)."""
    if not attachment or not getattr(attachment, "name", None):
        return False
    ext = (attachment.name or "").rsplit(".", 1)[-1].lower()
    return ext in VIDEO_EXTENSIONS


@register.filter
def worker_report_attachment_is_video(attachment) -> bool:
    """True, если вложение отчёта воркера — видео (по расширению)."""
    if not attachment or not getattr(attachment, "name", None):
        return False
    ext = (attachment.name or "").rsplit(".", 1)[-1].lower()
    return ext in VIDEO_EXTENSIONS


@register.filter
def attachment_s3_url(attachment) -> str:
    """Возвращает прямой URL вложения (S3 или локальный). Для data-атрибутов в шаблоне."""
    if not attachment or not getattr(attachment, "name", None):
        return ""
    try:
        return attachment.url
    except Exception:
        return ""
