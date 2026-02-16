from __future__ import annotations

from uuid import uuid4

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class User(AbstractUser):
    """Кастомный пользователь с ролью и статусом модерации.

    Роли:
      - user     — обычный пользователь, работает с базами и лидами;
      - support  — сотрудник поддержки;
      - admin    — администратор, управляет базами и пользователями.

    Статусы:
      - pending  — ожидает одобрения;
      - approved — может получать базы и сдавать лиды;
      - banned   — доступ в кабинет ограничен.
    """

    class Role(models.TextChoices):
        USER = "user", "Пользователь"
        SUPPORT = "support", "Поддержка"
        ADMIN = "admin", "Администратор"

    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает одобрения"
        APPROVED = "approved", "Одобрен"
        BANNED = "banned", "Заблокирован"

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.USER,
        help_text="Роль в системе (права доступа).",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        help_text="Статус модерации пользователя.",
    )
    telegram_id = models.BigIntegerField(
        null=True,
        blank=True,
        unique=True,
        help_text="При необходимости — связка с Telegram-пользователем.",
    )
    balance = models.IntegerField(
        default=0,
        help_text="Баланс пользователя (руб.). Начисляется вручную админом.",
    )

    def is_approved(self) -> bool:
        return self.status == self.Status.APPROVED

    @property
    def is_support(self) -> bool:
        return self.role in {self.Role.SUPPORT, self.Role.ADMIN}


class TimeStampedModel(models.Model):
    """Базовая абстракция с датами создания/обновления."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class BaseType(models.Model):
    """Тип базы контактов (Telegram, WhatsApp и т.п.)."""

    slug = models.SlugField(
        unique=True,
        help_text="Системное имя (telegram, whatsapp, max и т.д.).",
    )
    name = models.CharField(max_length=100, help_text="Человекочитаемое название базы.")
    default_daily_limit = models.PositiveIntegerField(
        default=0,
        help_text="Базовый дневной лимит выдачи контактов одному пользователю.",
    )
    order = models.PositiveIntegerField(default=0, help_text="Порядок отображения в списках.")

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "Тип базы"
        verbose_name_plural = "Типы баз"

    def __str__(self) -> str:  # pragma: no cover - простое представление
        return self.name


class Contact(TimeStampedModel):
    """Контакт в конкретной базе (аналог строки в CSV файлах base_*.csv)."""

    base_type = models.ForeignKey(
        BaseType,
        on_delete=models.CASCADE,
        related_name="contacts",
    )
    value = models.CharField(
        max_length=255,
        help_text="Сырые данные контакта (юзернейм, телефон, ссылка и т.п.).",
    )
    assigned_to = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="issued_contacts",
        help_text="Пользователь, которому выдан контакт.",
    )
    assigned_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Когда контакт был выдан пользователю.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Можно ли контакт выдавать пользователям.",
    )

    class Meta:
        unique_together = ("base_type", "value")
        verbose_name = "Контакт"
        verbose_name_plural = "Контакты"

    def __str__(self) -> str:  # pragma: no cover - простое представление
        return f"{self.base_type.slug}: {self.value}"


class UserBaseLimit(models.Model):
    """Дополнительные лимиты по базам для конкретного пользователя.

    Аналог user_limits.csv из бота.
    """

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="base_limits",
    )
    base_type = models.ForeignKey(
        BaseType,
        on_delete=models.CASCADE,
        related_name="user_limits",
    )
    extra_daily_limit = models.PositiveIntegerField(
        default=0,
        help_text="Дополнительные контакты в день сверх базового лимита.",
    )

    class Meta:
        unique_together = ("user", "base_type")
        verbose_name = "Дополнительный лимит по базе"
        verbose_name_plural = "Дополнительные лимиты по базам"

    def __str__(self) -> str:  # pragma: no cover - простое представление
        return f"{self.user} – {self.base_type}: +{self.extra_daily_limit}"


class LeadType(models.Model):
    """Тип лида (аналог LEAD_TYPES в боте)."""

    slug = models.SlugField(unique=True, help_text="Системное имя типа лида.")
    name = models.CharField(max_length=100, help_text="Человекочитаемое название типа.")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "Тип лида"
        verbose_name_plural = "Типы лидов"

    def __str__(self) -> str:  # pragma: no cover - простое представление
        return self.name


def lead_attachment_upload_to(instance: "Lead", filename: str) -> str:
    """Путь для загрузки файлов по лидам."""
    ext = filename.split(".")[-1] if "." in filename else "bin"
    return f"leads/user_{instance.user_id}/{uuid4().hex}.{ext}"


class Lead(TimeStampedModel):
    """Лид / результат работы пользователя с базами."""

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="leads",
    )
    base_type = models.ForeignKey(
        BaseType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="leads",
        help_text="Из какой базы взят контакт (если известно).",
    )
    contact = models.ForeignKey(
        Contact,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="leads",
        help_text="Связанный контакт (если он есть в базе).",
    )
    raw_contact = models.CharField(
        max_length=255,
        blank=True,
        help_text="Текстовое значение контакта (если нет связи с моделью Contact).",
    )
    normalized_contact = models.CharField(
        max_length=255,
        blank=True,
        db_index=True,
        help_text="Нормализованный контакт для проверки дубликатов по всей базе (@user→user, ссылки без протокола).",
    )
    lead_type = models.ForeignKey(
        LeadType,
        on_delete=models.PROTECT,
        related_name="leads",
    )
    source = models.CharField(
        max_length=255,
        blank=True,
        help_text="Источник лида или доп. описание (например, ссылка на объявление).",
    )
    lead_date = models.DateField(
        default=timezone.now,
        help_text="Дата лида (когда пользователь относит отчёт). По умолчанию — сегодня.",
    )
    comment = models.TextField(
        blank=True,
        help_text="Комментарий пользователя или менеджера.",
    )
    attachment = models.FileField(
        upload_to=lead_attachment_upload_to,
        null=True,
        blank=True,
        help_text="Скриншот/файл, подтверждающий лид.",
    )

    class Status(models.TextChoices):
        PENDING = "pending", "На проверке"
        APPROVED = "approved", "Одобрен"
        REJECTED = "rejected", "Отклонён"
        REWORK = "rework", "На доработке"

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        help_text="Статус модерации лида.",
    )
    rejection_reason = models.TextField(
        blank=True,
        help_text="Причина отклонения (если статус «Отклонён»).",
    )
    rework_comment = models.TextField(
        blank=True,
        help_text="Что доработать (если статус «На доработке»).",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True, help_text="Когда модератор вынес решение.")
    reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_leads",
        help_text="Кто проверил лид.",
    )

    class Meta:
        verbose_name = "Лид"
        verbose_name_plural = "Лиды"

    def __str__(self) -> str:  # pragma: no cover - простое представление
        return f"Лид #{self.pk} от {self.user}"


class SupportThread(TimeStampedModel):
    """Диалог пользователя с поддержкой (аналог топика в SUPPORT_GROUP_ID)."""

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="support_threads",
    )
    is_closed = models.BooleanField(default=False)
    last_read_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Когда сотрудник поддержки последний раз открывал диалог (для пометки «прочитано»).",
    )
    user_last_read_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Когда пользователь последний раз открывал чат (для уведомления о новых сообщениях от поддержки).",
    )

    class Meta:
        verbose_name = "Диалог поддержки"
        verbose_name_plural = "Диалоги поддержки"

    def __str__(self) -> str:  # pragma: no cover - простое представление
        return f"Поддержка: {self.user} ({'закрыт' if self.is_closed else 'активен'})"


class SupportMessage(TimeStampedModel):
    """Сообщение в диалоге поддержки."""

    thread = models.ForeignKey(
        SupportThread,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="support_messages",
        help_text="Автор сообщения (пользователь или сотрудник поддержки).",
    )
    is_from_support = models.BooleanField(
        default=False,
        help_text="True, если сообщение от поддержки/менеджера.",
    )
    text = models.TextField(blank=True)
    attachment = models.FileField(
        upload_to="support/%Y/%m/%d/",
        null=True,
        blank=True,
        help_text="Вложение: скриншот, файл.",
    )

    class Meta:
        verbose_name = "Сообщение поддержки"
        verbose_name_plural = "Сообщения поддержки"

    def __str__(self) -> str:  # pragma: no cover - простое представление
        return f"Сообщение в {self.thread_id}"


class ContactRequest(TimeStampedModel):
    """Заявка пользователя на дополнительный лимит контактов (кнопка «Обратиться»)."""

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="contact_requests",
    )
    base_type = models.ForeignKey(
        BaseType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contact_requests",
        help_text="База, по которой запрашивает лимит (если известна).",
    )
    status = models.CharField(
        max_length=20,
        choices=[("pending", "Ожидает"), ("resolved", "Обработано")],
        default="pending",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="resolved_contact_requests",
    )

    class Meta:
        verbose_name = "Заявка на контакты"
        verbose_name_plural = "Заявки на контакты"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Заявка от {self.user} ({self.get_status_display()})"


class WithdrawalRequest(TimeStampedModel):
    """Заявка пользователя на вывод средств (баланс)."""

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="withdrawal_requests",
    )
    amount = models.PositiveIntegerField(help_text="Сумма к выводу (руб.)")
    payout_details = models.TextField(
        help_text="Способ вывода: номер карты, телефон с банком и т.п.",
        blank=True,
    )
    status = models.CharField(
        max_length=20,
        choices=[("pending", "На рассмотрении"), ("approved", "Выполнено"), ("rejected", "Отклонено")],
        default="pending",
    )
    processed_at = models.DateTimeField(null=True, blank=True)
    processed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="processed_withdrawals",
    )

    class Meta:
        verbose_name = "Заявка на вывод"
        verbose_name_plural = "Заявки на вывод"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Вывод {self.amount} от {self.user} ({self.get_status_display()})"


class BasesImportJob(TimeStampedModel):
    """Статус фонового импорта баз из Excel (все листы). Один запуск — одна запись."""

    class Status(models.TextChoices):
        RUNNING = "running", "В процессе"
        SUCCESS = "success", "Завершён"
        ERROR = "error", "Ошибка"

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.RUNNING,
    )
    message = models.TextField(blank=True, help_text="Результат или текст ошибки.")
    started_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bases_import_jobs",
    )

    class Meta:
        verbose_name = "Импорт баз"
        verbose_name_plural = "Импорты баз"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Импорт {self.get_status_display()} ({self.created_at})"


class MediaStorageConfig(models.Model):
    """Настройки S3 для медиафайлов (вложения лидов). Одна запись — конфиг с сайта, без переменных окружения.
    Если включено и заполнено — загрузки сохраняются в S3 и не теряются при редеплое."""
    enabled = models.BooleanField(default=False, help_text="Включить хранение медиа в S3")
    bucket_name = models.CharField(max_length=255, blank=True, help_text="Имя бакета (Timeweb Cloud S3)")
    access_key_id = models.CharField(max_length=255, blank=True, verbose_name="Access Key ID")
    secret_access_key = models.CharField(max_length=255, blank=True, verbose_name="Secret Access Key")
    endpoint_url = models.URLField(max_length=500, blank=True, help_text="Например https://s3.timeweb.cloud")
    region_name = models.CharField(max_length=64, blank=True, default="ru-1")

    class Meta:
        verbose_name = "Настройки хранилища медиа (S3)"
        verbose_name_plural = "Настройки хранилища медиа (S3)"

    def __str__(self) -> str:
        return f"S3: {self.bucket_name or 'не задано'}" + (" (вкл.)" if self.enabled else " (выкл.)")

