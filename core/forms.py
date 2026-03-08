from __future__ import annotations

from django import forms
from django.contrib.auth.forms import UserCreationForm

from .models import BaseType, Lead, LeadType, User, WorkerSelfLead


class UserRegistrationForm(UserCreationForm):
    """Форма регистрации нового пользователя.

    Пользователь создаётся со статусом `pending` (по умолчанию в модели),
    далее его можно одобрить через админку/интерфейс поддержки.
    """

    username = forms.CharField(
        label="Логин",
        help_text="Укажите свой Telegram @ник (можно без символа @).",
    )
    password1 = forms.CharField(
        label="Пароль",
        strip=False,
        widget=forms.PasswordInput,
        help_text="Любой пароль, который вам удобно запомнить. Не передавайте его другим людям.",
    )
    password2 = forms.CharField(
        label="Повтор пароля",
        strip=False,
        widget=forms.PasswordInput,
        help_text="Введите тот же пароль ещё раз для проверки.",
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username",)

    def clean_username(self) -> str:
        username = self.cleaned_data.get("username", "").strip()
        if username.startswith("@"):
            username = username[1:]
        return username


class BaseRequestForm(forms.Form):
    """Выбор типа базы для получения контактов."""

    base_type = forms.ModelChoiceField(
        label="Тип базы",
        queryset=BaseType.objects.all().order_by("order"),
        widget=forms.RadioSelect,
        empty_label=None,
    )


# Расширения вложений лида: изображения и видео (запись экрана iPhone/Android)
LEAD_ATTACHMENT_IMAGE_EXTENSIONS = frozenset(("jpg", "jpeg", "png", "gif", "webp", "bmp", "heic"))
LEAD_ATTACHMENT_VIDEO_EXTENSIONS = frozenset(("mp4", "mov", "webm", "m4v", "3gp", "mkv", "avi", "hevc"))
LEAD_ATTACHMENT_ALLOWED_EXTENSIONS = LEAD_ATTACHMENT_IMAGE_EXTENSIONS | LEAD_ATTACHMENT_VIDEO_EXTENSIONS
# Максимальный размер вложения (30 МБ), чтобы тяжёлые видео не ломали загрузку
LEAD_ATTACHMENT_MAX_SIZE = 30 * 1024 * 1024
# Единый атрибут accept для всех file input с вложениями лидов/отчётов
_ATTACHMENT_ACCEPT = "image/*,video/*,.mp4,.mov,.webm,.m4v,.3gp,.mkv,.avi,.hevc,.heic"


class LeadReportForm(forms.ModelForm):
    """Форма отправки лида/отчёта пользователем. Одно поле «Контакт / ссылка» сохраняется в raw_contact и source."""

    raw_contact = forms.CharField(
        label="Контакт / ссылка",
        help_text="Юзернейм, телефон, ссылка на объявление или другой идентификатор.",
    )

    class Meta:
        model = Lead
        fields = ("lead_type", "lead_date", "raw_contact", "attachment", "comment", "needs_team_contact")
        labels = {
            "lead_type": "Категория лида",
            "lead_date": "Дата лида",
            "comment": "Комментарий (необязательно)",
            "attachment": "Видео",
            "needs_team_contact": "Связаться самим",
        }
        help_texts = {
            "comment": "",
            "lead_date": "К какой дате относите отчёт. По умолчанию — сегодня. Обязательно.",
            "attachment": "Запись экрана (видео), подтверждающая лид (обязательно)",
            "needs_team_contact": "Отметьте, если команда должна связаться с контактом.",
        }
        widgets = {
            "lead_type": forms.Select(
                attrs={
                    "class": "form-select",
                    "style": "background-color: rgba(15, 23, 42, 0.95); border-color: rgba(55, 65, 81, 0.9); color: #e5e7eb;",
                }
            ),
            "lead_date": forms.DateInput(
                attrs={
                    "type": "date",
                    "class": "form-control",
                    "style": "background-color: rgba(15, 23, 42, 0.95); border: 1px solid rgba(55, 65, 81, 0.9); color: #e5e7eb;",
                }
            ),
            "raw_contact": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Контакт / ссылка",
                    "style": "background-color: rgba(15, 23, 42, 0.95); border: 1px solid rgba(55, 65, 81, 0.9); color: #e5e7eb;",
                }
            ),
            "attachment": forms.FileInput(
                attrs={
                    "class": "form-control",
                    "style": "background-color: rgba(15, 23, 42, 0.95); border-color: rgba(55, 65, 81, 0.9); color: #e5e7eb;",
                    "accept": _ATTACHMENT_ACCEPT,
                }
            ),
            "comment": forms.Textarea(
                attrs={
                    "rows": 2,
                    "class": "form-control",
                    "placeholder": "Комментарий (необязательно)",
                    "style": "background-color: rgba(15, 23, 42, 0.95); border: 1px solid rgba(55, 65, 81, 0.9); color: #e5e7eb;",
                }
            ),
            "needs_team_contact": forms.CheckboxInput(
                attrs={
                    "class": "form-check-input",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Пользовательский сайт: скрываем категорию «Самостоятельные лиды»
        # (slug='self'), чтобы её нельзя было выбрать в выпадающем списке.
        try:
            self.fields["lead_type"].queryset = LeadType.objects.exclude(slug="self").order_by("order", "id")
        except Exception:
            # На случай проблем с миграциями/БД — не ломаем форму, просто оставляем стандартный queryset.
            pass
        self.fields["attachment"].required = True
        self.fields["lead_date"].required = True
        if not self.instance or not self.instance.pk:
            from django.utils import timezone
            self.fields["lead_date"].initial = timezone.now().date()

    def clean_attachment(self):
        data = self.cleaned_data.get("attachment")
        if not data:
            raise forms.ValidationError("Обязательно приложите скриншот или видео, подтверждающие лид.")
        name = getattr(data, "name", None) or ""
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in LEAD_ATTACHMENT_ALLOWED_EXTENSIONS:
            raise forms.ValidationError(
                "Разрешены только изображения и видео (скриншот или запись экрана): jpg, png, gif, webp, mp4, mov и т.д."
            )
        size = getattr(data, "size", None)
        if size is not None and size > LEAD_ATTACHMENT_MAX_SIZE:
            raise forms.ValidationError(
                "Размер файла не должен превышать 30 МБ. Сожмите видео или приложите скриншот."
            )
        return data


class BaseExcelUploadForm(forms.Form):
    """Загрузка Excel с базами контактов — все листы по шаблону (как в боте)."""

    file = forms.FileField(
        label="Файл Excel с базами (.xlsx)",
        widget=forms.FileInput(attrs={"class": "form-control", "accept": ".xlsx"}),
    )


class BaseCategoryUploadForm(forms.Form):
    """Загрузка базы контактов в одну выбранную категорию."""

    base_type = forms.ModelChoiceField(
        label="Категория базы",
        queryset=BaseType.objects.all().order_by("order"),
        empty_label="— Выберите категорию —",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    file = forms.FileField(label="Файл Excel (.xlsx)", widget=forms.FileInput(attrs={"class": "form-control"}))


class LeadsExcelUploadForm(forms.Form):
    """Загрузка Excel с лидами (опционально, на будущее)."""

    file = forms.FileField(label="Файл Excel с лидами (.xlsx)")


class LeadRejectForm(forms.Form):
    """Форма отклонения лида — причина."""

    rejection_reason = forms.CharField(
        label="Причина отклонения",
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Укажите причину отклонения лида"}),
        required=True,
    )


class WorkerSelfLeadForm(forms.ModelForm):
    """Форма отправки лида исполнителем (самостоятельный лид)."""

    raw_contact = forms.CharField(
        label="Контакт / ссылка",
        help_text="Юзернейм, телефон, ссылка на объявление или другой идентификатор.",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "@username / +7..."}),
    )

    class Meta:
        model = WorkerSelfLead
        fields = ("raw_contact", "lead_date", "attachment", "comment")
        labels = {
            "lead_date": "Дата лида",
            "attachment": "Скриншот / видео",
            "comment": "Комментарий (необязательно)",
        }
        widgets = {
            "lead_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "attachment": forms.ClearableFileInput(attrs={"class": "form-control", "accept": _ATTACHMENT_ACCEPT}),
            "comment": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Дополнительная информация о лиде"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["lead_date"].required = True
        self.fields["attachment"].required = False
        if not self.instance or not self.instance.pk:
            from django.utils import timezone
            self.fields["lead_date"].initial = timezone.now().date()

    def clean_attachment(self):
        data = self.cleaned_data.get("attachment")
        if not data:
            return data
        name = getattr(data, "name", None) or ""
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in LEAD_ATTACHMENT_ALLOWED_EXTENSIONS:
            raise forms.ValidationError(
                "Разрешены только изображения и видео: jpg, png, gif, webp, mp4, mov и т.д."
            )
        size = getattr(data, "size", None)
        if size is not None and size > LEAD_ATTACHMENT_MAX_SIZE:
            raise forms.ValidationError("Размер файла не должен превышать 30 МБ.")
        return data


class WorkerSelfLeadReworkForm(forms.Form):
    """Форма доработки лида исполнителем."""

    raw_contact = forms.CharField(
        label="Контакт / ссылка",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    lead_date = forms.DateField(
        label="Дата лида",
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )
    attachment = forms.FileField(
        label="Скриншот / видео (необязательно)",
        required=False,
        widget=forms.ClearableFileInput(attrs={"class": "form-control", "accept": _ATTACHMENT_ACCEPT}),
    )
    comment = forms.CharField(
        label="Комментарий",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
    )

    def clean_attachment(self):
        data = self.cleaned_data.get("attachment")
        if not data:
            return data
        name = getattr(data, "name", None) or ""
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in LEAD_ATTACHMENT_ALLOWED_EXTENSIONS:
            raise forms.ValidationError("Разрешены только изображения и видео.")
        size = getattr(data, "size", None)
        if size is not None and size > LEAD_ATTACHMENT_MAX_SIZE:
            raise forms.ValidationError("Размер файла не должен превышать 30 МБ.")
        return data


class LeadReworkForm(forms.Form):
    """Форма отправки лида на доработку — что доработать."""

    rework_comment = forms.CharField(
        label="Что нужно доработать",
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Опишите, что нужно исправить или добавить"}),
        required=True,
    )


class WorkerReportForm(forms.Form):
    """Форма отправки отчёта исполнителем (воркером) по назначенному лиду."""

    raw_contact = forms.CharField(
        label="Контакт / результат",
        help_text="Контакт, ссылка или описание результата работы.",
        required=True,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Контакт / результат"}),
    )
    comment = forms.CharField(
        label="Комментарий",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Комментарий (необязательно)"}),
    )
    attachment = forms.FileField(
        label="Скриншот или видео",
        help_text="Фото или видео (MP4, MOV, WEBM, JPG, PNG, MKV и др.). Макс. 30 МБ. Если видео тяжелее — сожмите или сделайте скриншот.",
        required=False,
        widget=forms.FileInput(attrs={"class": "form-control", "accept": _ATTACHMENT_ACCEPT}),
    )

    def clean_attachment(self):
        data = self.cleaned_data.get("attachment")
        if not data:
            return data
        name = getattr(data, "name", None) or ""
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in LEAD_ATTACHMENT_ALLOWED_EXTENSIONS:
            raise forms.ValidationError("Разрешены только изображения и видео: jpg, png, gif, webp, mp4, mov, mkv и т.д.")
        size = getattr(data, "size", None)
        if size is not None and size > LEAD_ATTACHMENT_MAX_SIZE:
            raise forms.ValidationError("Размер файла не должен превышать 30 МБ. Сожмите видео или прикрепите скриншот.")
        return data


class WorkerReportReworkForm(forms.Form):
    """Форма доработки отчёта исполнителем (обновить контакт, комментарий и вложение)."""

    raw_contact = forms.CharField(
        label="Контакт / результат",
        required=True,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Контакт / результат"}),
    )
    comment = forms.CharField(
        label="Комментарий",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Комментарий (необязательно)"}),
    )
    attachment = forms.FileField(
        label="Скриншот или видео",
        help_text="Загрузите новое или оставьте текущее. Макс. 30 МБ. Если видео тяжелее — сожмите или сделайте скриншот.",
        required=False,
        widget=forms.FileInput(attrs={"class": "form-control", "accept": _ATTACHMENT_ACCEPT}),
    )

    def clean_attachment(self):
        data = self.cleaned_data.get("attachment")
        if not data:
            return data
        name = getattr(data, "name", None) or ""
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in LEAD_ATTACHMENT_ALLOWED_EXTENSIONS:
            raise forms.ValidationError("Разрешены только изображения и видео: jpg, png, gif, webp, mp4, mov, mkv и т.д.")
        size = getattr(data, "size", None)
        if size is not None and size > LEAD_ATTACHMENT_MAX_SIZE:
            raise forms.ValidationError("Размер файла не должен превышать 30 МБ. Сожмите видео или прикрепите скриншот.")
        return data


class LeadReworkUserForm(forms.Form):
    """Форма доработки лида пользователем: контакт/ссылка, дата, комментарий, новое вложение."""

    raw_contact = forms.CharField(
        label="Контакт / ссылка",
        help_text="Юзернейм, телефон, ссылка на объявление или другой идентификатор.",
        required=True,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Контакт / ссылка"}),
    )
    lead_date = forms.DateField(
        label="Дата лида",
        required=True,
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )
    comment = forms.CharField(
        label="Комментарий",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Комментарий (необязательно)"}),
    )
    attachment = forms.FileField(
        label="Скриншот или видео",
        help_text="Обязательно: либо оставьте текущее вложение, либо загрузите новое (макс. 30 МБ).",
        required=False,
        widget=forms.FileInput(attrs={"class": "form-control", "accept": "image/*,.mp4,.mov,.webm,.m4v,.3gp"}),
    )

    def clean_attachment(self):
        data = self.cleaned_data.get("attachment")
        if not data:
            return data
        name = getattr(data, "name", None) or ""
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in LEAD_ATTACHMENT_ALLOWED_EXTENSIONS:
            raise forms.ValidationError(
                "Разрешены только изображения и видео: jpg, png, gif, webp, mp4, mov и т.д."
            )
        size = getattr(data, "size", None)
        if size is not None and size > LEAD_ATTACHMENT_MAX_SIZE:
            raise forms.ValidationError(
                "Размер файла не должен превышать 30 МБ. Сожмите видео или приложите скриншот."
            )
        return data

