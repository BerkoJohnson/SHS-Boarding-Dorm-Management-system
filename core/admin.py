from django.contrib import admin
from django import forms
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
    SMSDelivery,
    HouseMaster,
    DisciplinaryRecord,
    UserProfile,
    RollCallSession,
    RollCallEntry,
    RollCallAbsenceFollowUp,
)
from .widgets import PrivateStudentPhotoInput


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'phone_number', 'created_at')
    list_filter = ('role',)
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'user__email', 'phone_number')


@admin.register(HouseMaster)
class HouseMasterAdmin(admin.ModelAdmin):
    list_display = ('staff_id', 'full_name', 'user', 'house', 'assigned_dormitory', 'is_senior_housemaster')
    list_filter = ('is_senior_housemaster', 'house')
    search_fields = ('staff_id', 'full_name', 'user__username', 'phone_number')


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
    list_filter = ('residency', 'status', 'gender', 'house', 'dormitory')
    search_fields = ('admission_no', 'full_name', 'index_number')


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
    readonly_fields = ("updated_at",)

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
        AcademicTerm,
    ]
)
