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
