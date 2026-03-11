"""Вспомогательные функции для лидов: автоопределение типа базы, сжатие скриншотов/видео, нормализация контактов."""
from __future__ import annotations

import io
import logging
import os
import re
import shutil
import subprocess
import tempfile
from typing import TYPE_CHECKING

from django.core.files.base import ContentFile
from django.db.models import Q

logger = logging.getLogger(__name__)

LEAD_VIDEO_EXTENSIONS = frozenset(("mp4", "mov", "webm", "m4v", "3gp"))

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


def extract_username_from_contact(normalized: str) -> str | None:
    """Извлекает «чистый» идентификатор из нормализованного контакта (без префикса платформы).
    telegram:marina_k → marina_k, vk:marina_k → marina_k, ig:marina_k → marina_k.
    Для телефонов и прочих — None (кросс-платформенная проверка не применима)."""
    if not normalized:
        return None
    for prefix in ("telegram:", "vk:", "ig:", "ok:"):
        if normalized.startswith(prefix):
            username = normalized[len(prefix):].strip().lower()
            if username and not username.startswith("id") and len(username) >= 3:
                return username
    return None


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


def _get_attachment_extension(attachment) -> str | None:
    """Возвращает расширение файла вложения в нижнем регистре."""
    name = getattr(attachment, "name", None) or ""
    if "." in name:
        return name.rsplit(".", 1)[-1].lower()
    return None


def _get_ffmpeg_path() -> str | None:
    """Путь к ffmpeg: imageio-ffmpeg (бандл) или системный."""
    try:
        import imageio_ffmpeg
        path = imageio_ffmpeg.get_ffmpeg_exe()
        if path and os.path.isfile(path):
            return path
    except Exception:
        pass
    return shutil.which("ffmpeg")


def _get_video_duration(ffmpeg_exe: str, path: str) -> float | None:
    """Возвращает длительность видео в секундах или None при ошибке."""
    cmd = [
        ffmpeg_exe, "-i", path,
        "-f", "null", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "format=duration",
    ]
    # Более надёжный способ: ffprobe или парсинг stderr ffmpeg
    try:
        result = subprocess.run(
            [ffmpeg_exe, "-i", path],
            capture_output=True, text=True, timeout=10,
        )
        # ffmpeg пишет Duration: HH:MM:SS.xx в stderr
        for line in result.stderr.splitlines():
            if "Duration:" in line:
                part = line.split("Duration:")[1].split(",")[0].strip()
                parts = part.split(":")
                if len(parts) == 3:
                    h, m, s = float(parts[0]), float(parts[1]), float(parts[2])
                    return h * 3600 + m * 60 + s
    except Exception:
        pass
    return None


def _compress_video_ffmpeg(input_path: str, output_path: str, timeout: int = 300) -> bool:
    """Сжимает видео через ffmpeg: H.264, CRF 26, макс. 720p. Мягкое сжатие."""
    ffmpeg_exe = _get_ffmpeg_path()
    if not ffmpeg_exe:
        logger.warning("ffmpeg не найден (imageio-ffmpeg или системный) — сжатие видео пропущено")
        return False

    # Получаем длительность оригинала для валидации
    orig_duration = _get_video_duration(ffmpeg_exe, input_path)

    cmd = [
        ffmpeg_exe,
        "-y",
        "-i",
        input_path,
        "-vf",
        "scale='min(720,iw)':-2",
        "-c:v",
        "libx264",
        "-crf",
        "26",
        "-preset",
        "medium",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        output_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            return False
        # Валидация: длительность сжатого видео не должна быть сильно короче оригинала
        if orig_duration and orig_duration > 5:
            out_duration = _get_video_duration(ffmpeg_exe, output_path)
            if out_duration and out_duration < orig_duration * 0.8:
                logger.warning(
                    "Сжатое видео короче оригинала: %.1fs → %.1fs, отклоняем сжатие",
                    orig_duration, out_duration,
                )
                return False
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        logger.warning("Ошибка ffmpeg при сжатии видео: %s", e)
        return False


def _compress_video_local(path: str) -> bool:
    """Сжимает видео по локальному пути. Перезаписывает файл, если результат меньше."""
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if ext not in LEAD_VIDEO_EXTENSIONS:
        return False
    fd, out_path = tempfile.mkstemp(suffix=".mp4")
    try:
        os.close(fd)
        if not _compress_video_ffmpeg(path, out_path):
            return False
        orig_size = os.path.getsize(path)
        new_size = os.path.getsize(out_path)
        if new_size < orig_size:
            with open(out_path, "rb") as f:
                with open(path, "wb") as w:
                    w.write(f.read())
            return True
        return False
    finally:
        if os.path.exists(out_path):
            try:
                os.unlink(out_path)
            except OSError:
                pass


def _compress_video_remote(lead) -> bool:
    """Сжимает видео из S3/удалённого хранилища: скачать → ffmpeg → загрузить обратно."""
    storage = lead.attachment.storage
    name = lead.attachment.name
    ext = _get_attachment_extension(lead.attachment)
    if ext not in LEAD_VIDEO_EXTENSIONS:
        return False
    tmp_in = None
    tmp_out = None
    try:
        with storage.open(name, "rb") as f:
            data = f.read()
        fd_in, tmp_in = tempfile.mkstemp(suffix="." + (ext or "mp4"))
        os.write(fd_in, data)
        os.close(fd_in)
        fd_out, tmp_out = tempfile.mkstemp(suffix=".mp4")
        os.close(fd_out)
        if not _compress_video_ffmpeg(tmp_in, tmp_out):
            return False
        with open(tmp_out, "rb") as f:
            compressed = f.read()
        if len(compressed) >= len(data):
            return False
        try:
            storage.delete(name)
        except Exception:
            pass
        new_name = storage.save(name, ContentFile(compressed))
        if new_name != name:
            lead.attachment.name = new_name
            lead.save(update_fields=["attachment"])
        return True
    except Exception as e:
        logger.warning("Ошибка при сжатии видео (S3): %s", e)
        return False
    finally:
        for p in (tmp_in, tmp_out):
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass


def compress_lead_attachment(lead) -> bool:
    """Сжимает файл вложения лида: изображения (PIL) или видео (ffmpeg).
    Перезаписывает файл на месте. Для S3 — скачивает, сжимает, загружает обратно.
    Возвращает True, если сжатие применено."""
    if not lead or not getattr(lead, "attachment", None) or not lead.attachment:
        return False

    ext = _get_attachment_extension(lead.attachment)

    # Видео — ffmpeg
    if ext in LEAD_VIDEO_EXTENSIONS:
        try:
            path = lead.attachment.path
            if path and os.path.exists(path):
                return _compress_video_local(path)
        except (AttributeError, NotImplementedError, OSError):
            pass
        return _compress_video_remote(lead)

    # Изображения — PIL
    try:
        from PIL import Image
    except ImportError:
        return False
    try:
        path = lead.attachment.path
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
