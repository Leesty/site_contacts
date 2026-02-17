from __future__ import annotations

from django import forms
from django.contrib.auth.forms import UserCreationForm

from .models import BaseType, Lead, LeadType, User


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
LEAD_ATTACHMENT_VIDEO_EXTENSIONS = frozenset(("mp4", "mov", "webm", "m4v", "3gp"))
LEAD_ATTACHMENT_ALLOWED_EXTENSIONS = LEAD_ATTACHMENT_IMAGE_EXTENSIONS | LEAD_ATTACHMENT_VIDEO_EXTENSIONS
# Максимальный размер вложения (30 МБ), чтобы тяжёлые видео не ломали загрузку
LEAD_ATTACHMENT_MAX_SIZE = 30 * 1024 * 1024


class LeadReportForm(forms.ModelForm):
    """Форма отправки лида/отчёта пользователем. Одно поле «Контакт / ссылка» сохраняется в raw_contact и source."""

    raw_contact = forms.CharField(
        label="Контакт / ссылка",
        help_text="Юзернейм, телефон, ссылка на объявление или другой идентификатор.",
    )

    class Meta:
        model = Lead
        fields = ("lead_type", "lead_date", "raw_contact", "attachment", "comment")
        labels = {
            "lead_type": "Категория лида",
            "lead_date": "Дата лида",
            "comment": "Комментарий (необязательно)",
            "attachment": "Файл / скриншот или видео",
        }
        help_texts = {
            "comment": "",
            "lead_date": "К какой дате относите отчёт. По умолчанию — сегодня. Обязательно.",
            "attachment": "Скриншот или запись экрана (видео), подтверждающие лид (обязательно)",
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
                    "accept": "image/*,.mp4,.mov,.webm,.m4v,.3gp",
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


class LeadReworkForm(forms.Form):
    """Форма отправки лида на доработку — что доработать."""

    rework_comment = forms.CharField(
        label="Что нужно доработать",
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Опишите, что нужно исправить или добавить"}),
        required=True,
    )


class LeadReworkUserForm(forms.Form):
    """Форма доработки лида пользователем: контакт/ссылка, комментарий, новое вложение."""

    raw_contact = forms.CharField(
        label="Контакт / ссылка",
        help_text="Юзернейм, телефон, ссылка на объявление или другой идентификатор.",
        required=True,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Контакт / ссылка"}),
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

