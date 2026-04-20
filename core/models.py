from __future__ import annotations

from uuid import uuid4

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class User(AbstractUser):
    """Кастомный пользователь с ролью и статусом модерации.

    Роли:
      - user             — обычный пользователь, работает с базами и лидами;
      - support          — сотрудник поддержки;
      - admin            — администратор, управляет базами и пользователями;
      - standalone_admin — самостоятельный админ (СС лиды);
      - worker           — исполнитель (работает с задачами самостоятельного админа).

    Статусы:
      - pending  — ожидает одобрения;
      - approved — может получать базы и сдавать лиды;
      - banned   — доступ в кабинет ограничен.
    """

    class Role(models.TextChoices):
        USER = "user", "Пользователь"
        SUPPORT = "support", "Поддержка"
        ADMIN = "admin", "Админ по отчётам"
        MAIN_ADMIN = "main_admin", "Главный админ"
        STANDALONE_ADMIN = "standalone_admin", "Самостоятельный админ"
        BALANCE_ADMIN = "balance_admin", "Баланс‑админ"
        WORKER = "worker", "Исполнитель"
        PARTNER = "partner", "Партнёр"

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
    is_accredited = models.BooleanField(
        default=False,
        help_text="Аккредитирован (галочка для вывода средств).",
    )
    balance = models.IntegerField(
        default=0,
        help_text="Баланс пользователя за Отдел поиска (руб.).",
    )
    dozhim_balance = models.IntegerField(
        default=0,
        help_text="Баланс пользователя за Отдел дожима (руб.).",
    )
    balance_admin_rate = models.DecimalField(
        max_digits=6, decimal_places=2, default=5,
        help_text="Ставка баланс-админа за одобренный лид (руб.).",
    )
    balance_admin_earnings_offset = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text="Коррекция заработка при смене ставки (руб.).",
    )
    standalone_admin_owner = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="workers",
        limit_choices_to={"role": "standalone_admin"},
        help_text="Самостоятельный админ, к которому привязан исполнитель (воркер).",
    )
    partner_owner = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="partner_users",
        limit_choices_to={"role__in": ["partner", "user"]},
        help_text="Партнёр, привлёкший этого пользователя.",
    )
    partner_rate = models.PositiveIntegerField(
        default=10,
        help_text="Ставка партнёра (руб.) за каждый одобренный лид реферала.",
    )
    partner_link = models.ForeignKey(
        "PartnerLink",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="registered_users",
        help_text="Реферальная ссылка, по которой зарегистрировался пользователь (для affiliate-ставки).",
    )
    ref_searchlink_enabled = models.BooleanField(
        default=False,
        help_text="Разрешён ли SearchLink для реферала (включается менеджером-рефовладельцем).",
    )
    ref_searchlink_manager_cut = models.PositiveIntegerField(
        default=30,
        help_text="Доля менеджера (руб.) с одобренного SearchLink-отчёта реферала. Реферал получает 100 - cut.",
    )
    ref_lead_reward = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Ставка рефералу за одобренный лид (руб.). Если задано — перекрывает ставку из PartnerLink.",
    )

    # СМЗ (самозанятость) — верификация для выплат
    smz_fio = models.CharField(max_length=255, blank=True, help_text="ФИО для СМЗ.")
    smz_not_self = models.BooleanField(default=False, help_text="Приём не на себя (чужие реквизиты).")
    smz_status = models.CharField(
        max_length=20,
        choices=[("none", "Не подано"), ("pending", "На проверке"), ("approved", "Одобрено"), ("rejected", "Отклонено")],
        default="none",
        help_text="Статус верификации СМЗ.",
    )
    smz_submitted_at = models.DateTimeField(null=True, blank=True, help_text="Дата подачи заявки на СМЗ.")
    smz_reject_reason = models.TextField(blank=True, help_text="Причина отклонения СМЗ.")

    def is_approved(self) -> bool:
        return self.status == self.Status.APPROVED

    @property
    def is_support(self) -> bool:
        return self.role in {self.Role.SUPPORT, self.Role.ADMIN, self.Role.MAIN_ADMIN}


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
        db_index=True,
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
        db_index=True,
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
    needs_team_contact = models.BooleanField(
        default=False,
        help_text="Пометка «Связаться самим» — команда должна связаться с контактом.",
    )
    ss_admin_status = models.CharField(
        max_length=20,
        null=True,
        blank=True,
        choices=(
            ("rejected", "Отказ"),
            ("in_progress", "В работе"),
            ("meeting", "Встреча"),
        ),
        help_text="Статус для самостоятельного админа (только одобренные СС-лиды).",
    )

    class Meta:
        verbose_name = "Лид"
        verbose_name_plural = "Лиды"

    def __str__(self) -> str:  # pragma: no cover - простое представление
        return f"Лид #{self.pk} от {self.user}"


class LeadReviewLog(TimeStampedModel):
    """История модерации лида: одобрен / отклонён / отправлен на доработку."""

    class Action(models.TextChoices):
        APPROVED = "approved", "Одобрено"
        REJECTED = "rejected", "Отклонено"
        REWORK = "rework", "На доработку"

    lead = models.ForeignKey(
        Lead,
        on_delete=models.CASCADE,
        related_name="review_logs",
    )
    admin = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="lead_review_logs",
    )
    action = models.CharField(max_length=20, choices=Action.choices)
    balance_admin_rate_snapshot = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True,
        help_text="Ставка баланс-админа на момент одобрения (руб.). NULL = старая ставка 5 руб.",
    )

    class Meta:
        verbose_name = "Событие модерации лида"
        verbose_name_plural = "События модерации лидов"

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.lead_id}: {self.get_action_display()} ({self.created_at})"


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
    receipt = models.FileField(
        upload_to="receipts/",
        null=True,
        blank=True,
        help_text="Чек СМЗ (скриншот).",
    )
    receipt_uploaded_at = models.DateTimeField(null=True, blank=True, help_text="Дата загрузки чека.")
    receipt_checked = models.BooleanField(default=False, help_text="Чек проверен админом (deprecated, use receipt_status).")
    receipt_status = models.CharField(
        max_length=20,
        choices=[("none", "Нет чека"), ("pending", "На проверке"), ("approved", "Одобрен"), ("rejected", "Отклонён")],
        default="none",
        help_text="Статус проверки чека.",
    )
    receipt_reject_reason = models.TextField(blank=True, help_text="Причина отклонения чека.")

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


def worker_report_upload_to(instance: "WorkerReport", filename: str) -> str:
    """Путь для загрузки файлов по отчётам исполнителей."""
    ext = filename.split(".")[-1] if "." in filename else "bin"
    return f"worker_reports/worker_{instance.worker_id}/{uuid4().hex}.{ext}"


class ReferralLink(TimeStampedModel):
    """Реферальная ссылка для регистрации исполнителей (воркеров) через самостоятельного админа."""

    standalone_admin = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="referral_links",
        limit_choices_to={"role": "standalone_admin"},
    )
    code = models.CharField(max_length=32, unique=True, help_text="Уникальный код ссылки (случайный).")
    is_active = models.BooleanField(default=True, help_text="Активна ли ссылка для регистрации.")
    note = models.CharField(max_length=100, blank=True, help_text="Заметка для идентификации ссылки.")

    class Meta:
        verbose_name = "Реферальная ссылка"
        verbose_name_plural = "Реферальные ссылки"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Ref:{self.code} ({self.standalone_admin})"


class LeadAssignment(TimeStampedModel):
    """Назначение лида исполнителю (воркеру) самостоятельным админом."""

    lead = models.ForeignKey(
        Lead,
        on_delete=models.CASCADE,
        related_name="assignments",
    )
    worker = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="lead_assignments",
        limit_choices_to={"role": "worker"},
    )
    assigned_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="assigned_leads_by",
    )
    task_description = models.TextField(blank=True, help_text="Описание задачи для исполнителя.")
    refused = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Лид отказался, нужно связаться Артёму.",
    )
    refused_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("lead", "worker")
        verbose_name = "Назначение лида"
        verbose_name_plural = "Назначения лидов"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Лид #{self.lead_id} → @{self.worker.username}"


class WorkerReport(TimeStampedModel):
    """Отчёт исполнителя (воркера) по назначенному лиду."""

    class Status(models.TextChoices):
        PENDING = "pending", "На проверке"
        APPROVED = "approved", "Одобрен"
        REJECTED = "rejected", "Отклонён"
        REWORK = "rework", "На доработке"

    assignment = models.OneToOneField(
        LeadAssignment,
        on_delete=models.CASCADE,
        related_name="report",
    )
    worker = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="worker_reports",
    )
    standalone_admin = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="received_worker_reports",
    )
    raw_contact = models.CharField(max_length=255, help_text="Контакт / результат работы.")
    comment = models.TextField(blank=True)
    attachment = models.FileField(
        upload_to=worker_report_upload_to,
        blank=True,
        null=True,
        help_text="Скриншот/видео подтверждения.",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    rework_comment = models.TextField(blank=True, help_text="Что исправить (при статусе «На доработке»).")
    rejection_reason = models.TextField(blank=True)
    reward = models.PositiveIntegerField(default=40, help_text="Вознаграждение за одобренный отчёт (руб.).")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_worker_reports",
    )

    class Meta:
        verbose_name = "Отчёт исполнителя"
        verbose_name_plural = "Отчёты исполнителей"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Отчёт @{self.worker.username} по лиду #{self.assignment.lead_id} ({self.get_status_display()})"


class WorkerWithdrawalRequest(TimeStampedModel):
    """Заявка исполнителя (воркера) на вывод средств — обрабатывается самостоятельным админом."""

    worker = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="worker_withdrawal_requests",
    )
    standalone_admin = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="worker_withdrawals_to_process",
    )
    amount = models.PositiveIntegerField(help_text="Сумма к выводу (руб.)")
    payout_details = models.TextField(help_text="Реквизиты для вывода.")
    status = models.CharField(
        max_length=20,
        choices=[("pending", "На рассмотрении"), ("approved", "Выплачено"), ("rejected", "Отклонено")],
        default="pending",
    )
    processed_at = models.DateTimeField(null=True, blank=True)
    processed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="processed_worker_withdrawals",
    )

    class Meta:
        verbose_name = "Заявка воркера на вывод"
        verbose_name_plural = "Заявки воркеров на вывод"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Вывод {self.amount} руб. от @{self.worker.username} ({self.get_status_display()})"


def worker_self_lead_upload_to(instance: "WorkerSelfLead", filename: str) -> str:
    """Путь для загрузки вложений к самостоятельным лидам исполнителей."""
    ext = filename.split(".")[-1] if "." in filename else "bin"
    return f"worker_self_leads/worker_{instance.worker_id}/{uuid4().hex}.{ext}"


class WorkerSelfLead(TimeStampedModel):
    """Лид, самостоятельно отправленный исполнителем (воркером) своему СС-админу на проверку."""

    class Status(models.TextChoices):
        PENDING = "pending", "На проверке"
        APPROVED = "approved", "Одобрен"
        REJECTED = "rejected", "Отклонён"
        REWORK = "rework", "На доработке"

    worker = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="self_leads",
        limit_choices_to={"role": "worker"},
    )
    standalone_admin = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="received_worker_self_leads",
        limit_choices_to={"role": "standalone_admin"},
    )
    raw_contact = models.CharField(max_length=500, help_text="Контакт / ссылка (юзернейм, телефон и т.д.).")
    lead_date = models.DateField(help_text="Дата лида.")
    attachment = models.FileField(
        upload_to=worker_self_lead_upload_to,
        blank=True,
        null=True,
        help_text="Скриншот или видео подтверждения.",
    )
    comment = models.TextField(blank=True, help_text="Комментарий к лиду.")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    rework_comment = models.TextField(blank=True, help_text="Что исправить (при статусе «На доработке»).")
    rejection_reason = models.TextField(blank=True)
    reward = models.PositiveIntegerField(default=40, help_text="Вознаграждение за одобренный лид (руб.).")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_worker_self_leads",
    )

    class Meta:
        verbose_name = "Лид от исполнителя"
        verbose_name_plural = "Лиды от исполнителей"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Лид от @{self.worker.username}: {self.raw_contact} ({self.get_status_display()})"


def partner_link_code() -> str:
    """Генерирует уникальный код для партнёрской реферальной ссылки."""
    from uuid import uuid4
    return uuid4().hex[:24]


class PartnerLink(TimeStampedModel):
    """Реферальная ссылка партнёрского админа для привлечения пользователей."""

    partner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="partner_links",
        limit_choices_to={"role__in": ["partner", "user"]},
    )
    code = models.CharField(max_length=32, unique=True, default=partner_link_code)
    is_active = models.BooleanField(default=True)
    note = models.CharField(max_length=100, blank=True, help_text="Заметка для идентификации ссылки.")
    ref_reward = models.PositiveIntegerField(
        default=20,
        help_text="Ставка рефу за одобренный лид (руб.). Партнёр получает 40 - ref_reward.",
    )

    class Meta:
        verbose_name = "Реферальная ссылка партнёра"
        verbose_name_plural = "Реферальные ссылки партнёров"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"PartnerLink({self.code}) → @{self.partner.username}"


class DozhimIssuedLead(TimeStampedModel):
    """Одобренный лид из Отдела поиска, выданный пользователю для дожима."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="dozhim_issued_leads")
    lead = models.ForeignKey("Lead", on_delete=models.CASCADE, related_name="dozhim_issues")

    class Meta:
        unique_together = ("user", "lead")
        verbose_name = "Выданный лид для дожима"
        verbose_name_plural = "Выданные лиды для дожима"

    def __str__(self) -> str:
        return f"DozhimIssued(user={self.user_id}, lead={self.lead_id})"


class PartnerEarning(TimeStampedModel):
    """Начисление партнёру за одобренный лид привлечённого пользователя."""

    partner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="partner_earnings",
    )
    lead = models.OneToOneField(
        "Lead",
        on_delete=models.SET_NULL,
        related_name="partner_earning",
        null=True,
        blank=True,
    )
    search_report = models.OneToOneField(
        "SearchReport",
        on_delete=models.SET_NULL,
        related_name="partner_earning",
        null=True,
        blank=True,
    )
    amount = models.PositiveIntegerField(default=10)

    class Meta:
        verbose_name = "Начисление партнёру"
        verbose_name_plural = "Начисления партнёрам"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"+{self.amount} руб. → @{self.partner.username}"


def site_settings_upload_to(instance, filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "mp4"
    return f"site/{uuid4().hex[:12]}.{ext}"


class SiteSettings(models.Model):
    """Настройки сайта. Одна запись на весь сайт."""
    example_video = models.FileField(
        upload_to=site_settings_upload_to,
        null=True,
        blank=True,
        help_text="Видео-пример идеального отчёта. Загрузите MP4/MOV/WEBM.",
    )
    example_video_description = models.CharField(
        max_length=500,
        blank=True,
        default="Посмотрите пример идеального видео-отчёта",
        help_text="Текст ссылки на пример видео.",
    )
    auto_approve_users = models.BooleanField(
        default=False,
        help_text="Автоматически одобрять новых пользователей при регистрации.",
    )

    class Meta:
        verbose_name = "Настройки сайта"
        verbose_name_plural = "Настройки сайта"

    def __str__(self) -> str:
        return "Настройки сайта"

    @classmethod
    def get_settings(cls):
        """Получить или создать единственную запись настроек."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class BalanceLog(models.Model):
    """Лог всех операций с балансом пользователя."""

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="balance_logs",
    )
    field = models.CharField(
        max_length=20,
        default="balance",
        help_text="Какой баланс: balance или dozhim_balance.",
    )
    old_value = models.IntegerField(help_text="Баланс до операции.")
    new_value = models.IntegerField(help_text="Баланс после операции.")
    delta = models.IntegerField(help_text="Изменение (+ или -).")
    reason = models.CharField(max_length=255, help_text="Причина изменения.")
    actor = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="balance_actions",
        help_text="Кто выполнил действие (админ, система).",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Лог баланса"
        verbose_name_plural = "Логи баланса"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.user.username}: {self.field} {self.old_value}→{self.new_value} ({self.reason})"


def log_balance_change(user, field, old_value, new_value, reason, actor=None):
    """Записать изменение баланса в лог."""
    BalanceLog.objects.create(
        user=user,
        field=field,
        old_value=old_value,
        new_value=new_value,
        delta=new_value - old_value,
        reason=reason,
        actor=actor,
    )


# ─── SearchLink система ───────────────────────────────────────────────────────

import random

SEARCH_BOT_POOL = [
    "A0vqbot", "A1vqbot", "A2vqbot", "A3vqbot", "A4vqbot",
    "A6vqbot", "A7vqbot", "A8vqbot", "A9vqbot", "B0vqbot",
    "B1vqbot", "B2vqbot", "B5vqbot", "B10vqbot", "d4rcbot", "murzz_kl_bot",
]


def search_link_code() -> str:
    """Генерирует уникальный код для SearchLink."""
    return uuid4().hex[:16]


def search_report_upload_to(instance: "SearchReport", filename: str) -> str:
    """Путь для загрузки вложений к отчётам SearchLink."""
    ext = filename.split(".")[-1] if "." in filename else "bin"
    return f"search_reports/user_{instance.user_id}/{uuid4().hex}.{ext}"


VK_COMMUNITY_SCREEN_NAME = "leadmurzz"


class SearchLink(TimeStampedModel):
    """Ссылка для привлечения лидов через бота мессенджера (Telegram / VK)."""

    class Platform(models.TextChoices):
        TELEGRAM = "telegram", "Telegram"
        VK = "vk", "VK"

    display_id = models.PositiveIntegerField(
        null=True, blank=True, db_index=True, unique=True,
        help_text="Сквозной номер (общая нумерация с Lead).",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="search_links",
        help_text="Менеджер, создавший ссылку.",
    )
    code = models.CharField(
        max_length=32,
        unique=True,
        default=search_link_code,
        db_index=True,
        help_text="Уникальный код ссылки (/s/<code>/).",
    )
    platform = models.CharField(
        max_length=16,
        choices=Platform.choices,
        default=Platform.TELEGRAM,
        db_index=True,
        help_text="Платформа бота: Telegram или VK.",
    )
    lead_name = models.CharField(
        max_length=200,
        help_text="Имя/ник лида (для OG-тегов и персонализации).",
    )
    bot_username = models.CharField(
        max_length=64,
        blank=True,
        help_text="Для TG — username бота из пула (без @). Для VK — screen_name community.",
    )
    bot_started = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Лид запустил бота (подтверждено вебхуком).",
    )
    bot_started_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Когда лид запустил бота.",
    )
    telegram_id = models.BigIntegerField(
        null=True,
        blank=True,
        help_text="Telegram ID лида (из вебхука).",
    )
    telegram_username = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Telegram username лида (без @), из вебхука при /start.",
    )
    telegram_first_name = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="Telegram first_name лида, из вебхука при /start.",
    )
    vk_user_id = models.BigIntegerField(
        null=True,
        blank=True,
        help_text="VK user id лида (из вебхука при первом сообщении community-боту).",
    )
    vk_screen_name = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="VK screen_name лида (nickname в URL vk.com/<screen_name>).",
    )
    vk_first_name = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="VK first_name лида, из вебхука.",
    )
    creator_ip = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text="IP менеджера при создании ссылки.",
    )
    visitor_ip = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text="IP посетителя при клике на лендинг.",
    )
    self_click = models.BooleanField(
        default=False,
        db_index=True,
        help_text="IP менеджера совпал с IP посетителя — подозрение на накрутку.",
    )

    class Meta:
        verbose_name = "SearchLink"
        verbose_name_plural = "SearchLinks"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"SearchLink({self.platform}:{self.code}) → {self.lead_name}"

    @property
    def deep_link(self) -> str:
        if self.platform == self.Platform.VK:
            # Один community на всю партнёрку, не пул — просто ref-параметр
            return f"https://vk.me/{self.bot_username or VK_COMMUNITY_SCREEN_NAME}?ref={self.code}"
        return f"https://t.me/{self.bot_username}?start={self.code}"

    @property
    def lead_contact_url(self) -> str:
        """Кликабельный профиль лида (для админки/менеджера)."""
        if self.platform == self.Platform.VK:
            if self.vk_screen_name:
                return f"https://vk.com/{self.vk_screen_name}"
            if self.vk_user_id:
                return f"https://vk.com/id{self.vk_user_id}"
            return ""
        if self.telegram_username:
            return f"https://t.me/{self.telegram_username}"
        return ""

    @property
    def lead_contact_display(self) -> str:
        if self.platform == self.Platform.VK:
            return self.vk_screen_name or (f"id{self.vk_user_id}" if self.vk_user_id else "")
        return self.telegram_username

    def save(self, *args, **kwargs):
        if not self.bot_username:
            if self.platform == self.Platform.VK:
                self.bot_username = VK_COMMUNITY_SCREEN_NAME
            else:
                self.bot_username = random.choice(SEARCH_BOT_POOL)
        if not self.display_id:
            from django.db.models import Max
            max_lead = Lead.objects.aggregate(m=Max("id"))["m"] or 0
            max_sl = SearchLink.objects.aggregate(m=Max("display_id"))["m"] or 0
            self.display_id = max(max_lead, max_sl) + 1
        super().save(*args, **kwargs)


class SearchReport(TimeStampedModel):
    """Отчёт менеджера, привязанный к SearchLink."""

    class Status(models.TextChoices):
        PENDING = "pending", "На проверке"
        APPROVED = "approved", "Одобрен"
        REJECTED = "rejected", "Отклонён"
        REWORK = "rework", "На доработке"

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="search_reports",
    )
    search_link = models.OneToOneField(
        SearchLink,
        on_delete=models.CASCADE,
        related_name="report",
        help_text="К какому SearchLink привязан отчёт.",
    )
    lead_date = models.DateField(
        default=timezone.now,
        help_text="Дата отчёта.",
    )
    attachment = models.FileField(
        upload_to=search_report_upload_to,
        null=True,
        blank=True,
        help_text="Скриншот/видео подтверждения.",
    )
    raw_contact = models.CharField(
        max_length=500,
        blank=True,
        help_text="Контакт / ссылка на клиента.",
    )
    comment = models.TextField(
        blank=True,
        help_text="Комментарий менеджера.",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    rejection_reason = models.TextField(blank=True)
    rework_comment = models.TextField(blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_search_reports",
    )
    paid_reward = models.IntegerField(
        default=0,
        help_text="Сколько реально начислено менеджеру на момент одобрения (без партнёрского cut). Для старых/не одобренных записей — 0.",
    )

    class Meta:
        verbose_name = "Отчёт SearchLink"
        verbose_name_plural = "Отчёты SearchLink"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"SearchReport #{self.pk} (link={self.search_link.code})"

