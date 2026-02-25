from __future__ import annotations

from django.contrib import admin

from . import models


@admin.register(models.User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("username", "email", "role", "status", "balance", "is_active", "is_staff")
    list_filter = ("role", "status", "is_staff", "is_superuser", "is_active")
    search_fields = ("username", "email", "telegram_id")
    ordering = ("username",)
    actions = ("mark_approved", "mark_banned", "mark_unbanned", "clear_contact_limits")

    def formfield_for_choice_field(self, db_field, request, **kwargs):
        if db_field.name == "role" and not request.user.is_superuser:
            choices = list(kwargs.get("choices", []))
            forbidden = {models.User.Role.STANDALONE_ADMIN, models.User.Role.BALANCE_ADMIN}
            kwargs["choices"] = [(k, v) for k, v in choices if k not in forbidden]
        return super().formfield_for_choice_field(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        if obj.role in (models.User.Role.STANDALONE_ADMIN, models.User.Role.BALANCE_ADMIN) and not request.user.is_superuser:
            from django.contrib import messages
            messages.error(request, "Роль «Самостоятельный админ» выдаётся только суперпользователем.")
            obj.role = form.initial.get("role") or models.User.Role.USER
        super().save_model(request, obj, form, change)

    @admin.action(description="Одобрить выбранных пользователей")
    def mark_approved(self, request, queryset):
        queryset.update(status=models.User.Status.APPROVED)

    @admin.action(description="Забанить выбранных пользователей")
    def mark_banned(self, request, queryset):
        queryset.update(status=models.User.Status.BANNED)

    @admin.action(description="Снять бан с выбранных пользователей")
    def mark_unbanned(self, request, queryset):
        queryset.update(status=models.User.Status.APPROVED)

    @admin.action(description="Очистить лимиты выдачи контактов для выбранных пользователей")
    def clear_contact_limits(self, request, queryset):
        models.UserBaseLimit.objects.filter(user__in=queryset).delete()


@admin.register(models.BaseType)
class BaseTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "default_daily_limit", "order")
    list_editable = ("default_daily_limit", "order")
    search_fields = ("name", "slug")


@admin.register(models.Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ("value", "base_type", "assigned_to", "assigned_at", "is_active")
    list_filter = ("base_type", "is_active")
    search_fields = ("value", "assigned_to__username")


@admin.register(models.UserBaseLimit)
class UserBaseLimitAdmin(admin.ModelAdmin):
    list_display = ("user", "base_type", "extra_daily_limit")
    list_filter = ("base_type",)
    search_fields = ("user__username", "user__email")


@admin.register(models.LeadType)
class LeadTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "order")
    list_editable = ("order",)
    search_fields = ("name", "slug")


@admin.register(models.Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "lead_type", "base_type", "status", "ss_admin_status", "contact", "created_at")
    list_filter = ("lead_type", "base_type", "status", "ss_admin_status", "created_at")
    search_fields = ("user__username", "user__email", "contact__value", "source")


@admin.register(models.LeadReviewLog)
class LeadReviewLogAdmin(admin.ModelAdmin):
    list_display = ("id", "lead", "admin", "action", "created_at")
    list_filter = ("action", "created_at")
    search_fields = ("lead__id", "admin__username")


@admin.register(models.SupportThread)
class SupportThreadAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "is_closed", "last_read_at", "created_at", "updated_at")
    list_filter = ("is_closed",)
    search_fields = ("user__username", "user__email")


@admin.register(models.SupportMessage)
class SupportMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "thread", "sender", "is_from_support", "created_at")
    list_filter = ("is_from_support", "created_at")
    search_fields = ("thread__user__username", "text")


@admin.register(models.BasesImportJob)
class BasesImportJobAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "started_by", "created_at")
    list_filter = ("status",)
    readonly_fields = ("status", "message", "started_by", "created_at", "updated_at")
    ordering = ("-created_at",)


@admin.register(models.WithdrawalRequest)
class WithdrawalRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "amount", "payout_details", "status", "created_at", "processed_at", "processed_by")
    list_filter = ("status", "created_at")
    search_fields = ("user__username",)
    readonly_fields = ("user", "amount", "payout_details", "created_at")


@admin.register(models.MediaStorageConfig)
class MediaStorageConfigAdmin(admin.ModelAdmin):
    list_display = ("id", "enabled", "bucket_name", "endpoint_url", "region_name")
    list_display_links = ("id", "bucket_name")
    list_editable = ("enabled",)
    fieldsets = (
        (None, {"fields": ("enabled",)}),
        (
            "S3 (Timeweb Cloud или другой)",
            {
                "fields": ("bucket_name", "access_key_id", "secret_access_key", "endpoint_url", "region_name"),
                "description": "Заполните и включите «enabled», чтобы загрузки (фото/видео лидов) сохранялись в S3 и не терялись при редеплое. Endpoint для Timeweb: https://s3.timeweb.cloud",
            },
        ),
    )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        from .storage import clear_media_config_cache
        clear_media_config_cache()

    def add_view(self, request, form_url="", extra_context=None):
        from django.shortcuts import redirect
        from django.urls import reverse
        obj = models.MediaStorageConfig.objects.first()
        if obj:
            return redirect(reverse("admin:core_mediastorageconfig_change", args=[obj.pk]))
        return super().add_view(request, form_url, extra_context)

