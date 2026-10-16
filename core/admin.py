from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as AuthUserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from django.core.exceptions import ValidationError
from django import forms
from django.conf import settings
from .models import (
    Student,
    Programme,
    House,
    Dormitory,
    DormitoryRoom,
    ClassGroup,
    Guardian,
    AcademicYear,
    AcademicTerm,
    Exeat,
    ExeatMovement,
    ExeatReconciliation,
    SMSConfiguration,
    SchoolConfiguration,
    ConfigurationAuditLog,
    SMSDelivery,
    HouseMaster,
    DisciplinaryRecord,
    UserProfile,
    StaffRole,
    get_user_profile,
    RollCallSession,
    RollCallEntry,
    RollCallAbsenceFollowUp,
)
from .widgets import PrivateStudentPhotoInput


class TenantStaffQuotaFormMixin:
    def clean_is_staff(self):
        is_staff = self.cleaned_data["is_staff"]
        if (
            is_staff
            and not self.instance.is_staff
            and settings.TENANCY_ENABLED
        ):
            from tenancy.entitlements import ensure_quota_available, quota_guard

            user_model = get_user_model()
            try:
                # The admin change view runs inside a transaction, so acquiring
                # the quota lock here holds it until the user row is written.
                with quota_guard("max_staff"):
                    ensure_quota_available(
                        "max_staff",
                        user_model.objects.filter(is_staff=True).count(),
                    )
            except ValidationError as error:
                raise forms.ValidationError(error.messages) from error
        return is_staff


class TenantUserChangeForm(TenantStaffQuotaFormMixin, UserChangeForm):
    pass


class TenantUserCreationForm(TenantStaffQuotaFormMixin, UserCreationForm):
    pass


class TenantUserAdmin(AuthUserAdmin):
    form = TenantUserChangeForm
    add_form = TenantUserCreationForm


user_model = get_user_model()
if admin.site.is_registered(user_model):
    admin.site.unregister(user_model)
admin.site.register(user_model, TenantUserAdmin)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'phone_number', 'created_at')
    list_filter = ('role',)
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'user__email', 'phone_number')

    def save_model(self, request, obj, form, change):
        previous_role = None
        if change:
            previous_role = UserProfile.objects.get(pk=obj.pk).role
        super().save_model(request, obj, form, change)
        if previous_role != obj.role:
            previous_label = (
                UserProfile(role=previous_role).get_role_display()
                if previous_role else "No role"
            )
            ConfigurationAuditLog.objects.create(
                actor=request.user,
                event=ConfigurationAuditLog.Event.STAFF_ROLE_CHANGED,
                description=(
                    f"{obj.user.get_username()}: {previous_label} to "
                    f"{obj.get_role_display()}."
                ),
                details={
                    "username": obj.user.get_username(),
                    "previous_role": previous_role,
                    "role": obj.role,
                },
            )


@admin.register(HouseMaster)
class HouseMasterAdmin(admin.ModelAdmin):
    list_display = ('staff_id', 'full_name', 'user', 'house', 'assigned_dormitory', 'is_senior_housemaster')
    list_filter = ('is_senior_housemaster', 'house')
    search_fields = ('staff_id', 'full_name', 'user__username', 'phone_number')

    def save_model(self, request, obj, form, change):
        tracked_fields = (
            "user_id",
            "house_id",
            "assigned_dormitory_id",
            "is_senior_housemaster",
        )
        previous = (
            HouseMaster.objects.get(pk=obj.pk)
            if change else None
        )
        changes = {}
        for field_name in tracked_fields:
            old_value = getattr(previous, field_name) if previous else None
            new_value = getattr(obj, field_name)
            if old_value != new_value:
                changes[field_name] = {
                    "from": str(old_value) if old_value is not None else None,
                    "to": str(new_value) if new_value is not None else None,
                }

        super().save_model(request, obj, form, change)
        if changes:
            ConfigurationAuditLog.objects.create(
                actor=request.user,
                event=ConfigurationAuditLog.Event.HOUSEMASTER_ASSIGNMENT_CHANGED,
                description=f"Housemaster assignment updated for {obj.full_name}.",
                details=changes,
            )


@admin.register(House)
class HouseAdmin(admin.ModelAdmin):
    list_display = ('name', 'display_order', 'motto')
    search_fields = ('name', 'motto')
    ordering = ('display_order', 'name')


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    class StudentForm(forms.ModelForm):
        class Meta:
            model = Student
            fields = "__all__"
            widgets = {"photo": PrivateStudentPhotoInput}

    form = StudentForm
    list_display = ('admission_no', 'full_name', 'gender', 'residency', 'status', 'house', 'dormitory', 'room', 'class_group')
    list_filter = (
        'residency',
        'status',
        'gender',
        'house',
        'dormitory',
        'room',
        'programme',
        'class_group',
    )
    search_fields = (
        'admission_no',
        'full_name',
        'index_number',
        'programme__name',
        'class_group__name',
        'house__name',
        'dormitory__name',
        'room__name',
    )
    list_per_page = 50


@admin.register(Exeat)
class ExeatAdmin(admin.ModelAdmin):
    list_display = ('exeat_number', 'student', 'exeat_type', 'expected_departure', 'expected_return', 'status', 'approved_by')
    list_filter = ('status', 'exeat_type', 'student__house')
    search_fields = ('exeat_number', 'student__full_name', 'student__admission_no')


@admin.register(ExeatMovement)
class ExeatMovementAdmin(admin.ModelAdmin):
    list_display = ('exeat', 'actual_departure', 'actual_return', 'departure_recorded_by', 'return_recorded_by')


@admin.register(ExeatReconciliation)
class ExeatReconciliationAdmin(admin.ModelAdmin):
    list_display = ('exeat', 'outcome', 'actual_departure', 'actual_return', 'reconciled_by', 'created_at')
    list_filter = ('outcome', 'created_at')
    search_fields = ('exeat__exeat_number', 'exeat__student__full_name', 'reason')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(SMSDelivery)
class SMSDeliveryAdmin(admin.ModelAdmin):
    list_display = ('event_type', 'recipient_name', 'phone_number', 'status', 'attempts', 'created_at')
    list_filter = ('event_type', 'status', 'created_at')
    search_fields = ('recipient_name', 'phone_number', 'exeat__exeat_number', 'exeat__student__full_name')
    readonly_fields = (
        'exeat', 'event_type', 'recipient_name', 'phone_number', 'message',
        'status', 'provider_reference', 'attempts', 'last_attempted_at',
        'sent_at', 'last_error', 'dedupe_key', 'scheduled_for',
        'created_at', 'updated_at',
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SMSConfiguration)
class SMSConfigurationAdmin(admin.ModelAdmin):
    list_display = ("enabled", "updated_at")
    fields = ("enabled", "updated_at")
    readonly_fields = ("enabled", "updated_at")

    def has_add_permission(self, request):
        return not SMSConfiguration.objects.filter(pk=1).exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        if SMSConfiguration.objects.filter(pk=1).exists():
            from django.shortcuts import redirect
            from django.urls import reverse

            return redirect(
                reverse("admin:core_smsconfiguration_change", args=[1])
            )
        return super().changelist_view(request, extra_context)


@admin.register(AcademicTerm)
class AcademicTermAdmin(admin.ModelAdmin):
    list_display = ("academic_year", "term", "level", "start_date", "end_date", "is_current")
    list_filter = ("academic_year", "term", "level", "is_current")

    def save_model(self, request, obj, form, change):
        tracked_fields = (
            "academic_year_id",
            "term",
            "level",
            "start_date",
            "end_date",
            "is_current",
        )
        previous = (
            AcademicTerm.objects.select_related("academic_year").get(pk=obj.pk)
            if change else None
        )
        old_values = {
            field_name: (
                getattr(previous, field_name).isoformat()
                if previous and field_name in {"start_date", "end_date"}
                else str(getattr(previous, field_name))
                if previous and field_name.endswith("_id")
                else getattr(previous, field_name)
                if previous else None
            )
            for field_name in tracked_fields
        }
        replaced_active_terms = list(
            AcademicTerm.objects.filter(level=obj.level, is_current=True)
            .exclude(pk=obj.pk)
            .values_list("pk", flat=True)
        )

        super().save_model(request, obj, form, change)
        new_values = {
            field_name: (
                getattr(obj, field_name).isoformat()
                if field_name in {"start_date", "end_date"}
                else str(getattr(obj, field_name))
                if field_name.endswith("_id")
                else getattr(obj, field_name)
            )
            for field_name in tracked_fields
        }
        if old_values != new_values:
            ConfigurationAuditLog.objects.create(
                actor=request.user,
                event=ConfigurationAuditLog.Event.ACADEMIC_TERM_CHANGED,
                description=f"Academic term {obj} saved in Django Admin.",
                details={
                    "from": old_values,
                    "to": new_values,
                    "replaced_active_terms": [
                        str(term_id) for term_id in replaced_active_terms
                    ],
                },
            )


@admin.register(SchoolConfiguration)
class SchoolConfigurationAdmin(admin.ModelAdmin):
    fields = ("name", "short_name", "logo", "address", "contact_number", "updated_at")
    readonly_fields = ("updated_at",)

    def has_add_permission(self, request):
        return not SchoolConfiguration.objects.filter(pk=1).exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        if SchoolConfiguration.objects.filter(pk=1).exists():
            from django.shortcuts import redirect
            from django.urls import reverse

            return redirect(
                reverse("admin:core_schoolconfiguration_change", args=[1])
            )
        return super().changelist_view(request, extra_context)

    def save_model(self, request, obj, form, change):
        tracked_fields = ("name", "short_name", "logo", "address", "contact_number")
        previous = (
            SchoolConfiguration.objects.get(pk=obj.pk)
            if change else None
        )
        changes = {}
        for field_name in tracked_fields:
            old_value = getattr(previous, field_name).name if (
                previous and field_name == "logo"
            ) else getattr(previous, field_name) if previous else None
            new_value = getattr(obj, field_name).name if field_name == "logo" else getattr(obj, field_name)
            if old_value != new_value:
                changes[field_name] = {"from": old_value, "to": new_value}

        super().save_model(request, obj, form, change)
        if changes:
            ConfigurationAuditLog.objects.create(
                actor=request.user,
                event=ConfigurationAuditLog.Event.SCHOOL_IDENTITY_CHANGED,
                description="School identity settings updated.",
                details=changes,
            )


@admin.register(ConfigurationAuditLog)
class ConfigurationAuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor", "event", "description")
    list_filter = ("event", "created_at")
    search_fields = ("description", "actor__username")
    readonly_fields = ("actor", "event", "description", "details", "created_at")
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        profile = get_user_profile(request.user)
        return bool(
            request.user.is_active
            and request.user.is_staff
            and (
                request.user.is_superuser
                or (profile and profile.role == StaffRole.ADMIN)
            )
        )


@admin.register(DisciplinaryRecord)
class DisciplinaryRecordAdmin(admin.ModelAdmin):
    list_display = (
        'student', 'offense', 'severity', 'is_corrected', 'action_taken',
        'logged_by', 'corrected_by', 'corrected_at', 'created_at',
    )
    readonly_fields = (
        'roll_call_entry',
        'roll_call_follow_up',
        'corrected_by',
        'corrected_at',
        'correction_note',
    )
@admin.register(RollCallSession)
class RollCallSessionAdmin(admin.ModelAdmin):
    list_display = ('house', 'dormitory', 'session_type', 'date', 'conducted_by', 'status')
    list_filter = ('session_type', 'status', 'house', 'date')
    search_fields = ('house__name', 'dormitory__name', 'conducted_by__full_name')


@admin.register(RollCallEntry)
class RollCallEntryAdmin(admin.ModelAdmin):
    list_display = ('session', 'student', 'status', 'status_source', 'remarks', 'gate_conflict_at')
    list_filter = ('status', 'session__house', 'session__date')
    search_fields = ('student__full_name', 'student__admission_no', 'remarks')

    def has_change_permission(self, request, obj=None):
        if obj and obj.session.status == 'Completed':
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.session.status == 'Completed':
            return False
        return super().has_delete_permission(request, obj)


@admin.register(RollCallAbsenceFollowUp)
class RollCallAbsenceFollowUpAdmin(admin.ModelAdmin):
    list_display = ('entry', 'status', 'reported_by', 'resolved_by', 'resolved_at')
    list_filter = ('status', 'entry__session__house', 'entry__session__date')
    search_fields = ('entry__student__full_name', 'entry__student__admission_no', 'resolution_note')
    readonly_fields = (
        'entry',
        'status',
        'reported_by',
        'resolved_by',
        'resolved_at',
        'resolution_note',
        'resulting_status',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


admin.site.register(
    [
        Programme,
        Dormitory,
        DormitoryRoom,
        ClassGroup,
        Guardian,
        AcademicYear,
    ]
)
