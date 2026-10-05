from django.contrib import admin
from .models import (
    Student,
    Programme,
    House,
    Dormitory,
    ClassGroup,
    Guardian,
    AcademicYear,
    AcademicTerm,
    Exeat,
    ExeatMovement,
    HouseMaster,
    DisciplinaryRecord,
    UserProfile,
    RollCallSession,
    RollCallEntry,
)


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


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ('admission_no', 'full_name', 'gender', 'residency', 'status', 'house', 'dormitory', 'class_group')
    list_filter = ('residency', 'status', 'gender', 'house')
    search_fields = ('admission_no', 'full_name', 'index_number')


@admin.register(Exeat)
class ExeatAdmin(admin.ModelAdmin):
    list_display = ('exeat_number', 'student', 'exeat_type', 'expected_departure', 'expected_return', 'status', 'approved_by')
    list_filter = ('status', 'exeat_type', 'student__house')
    search_fields = ('exeat_number', 'student__full_name', 'student__admission_no')


@admin.register(ExeatMovement)
class ExeatMovementAdmin(admin.ModelAdmin):
    list_display = ('exeat', 'actual_departure', 'actual_return', 'departure_recorded_by', 'return_recorded_by')


@admin.register(DisciplinaryRecord)
class DisciplinaryRecordAdmin(admin.ModelAdmin):
    list_display = ('student', 'offense', 'severity', 'action_taken', 'logged_by', 'created_at')
@admin.register(RollCallSession)
class RollCallSessionAdmin(admin.ModelAdmin):
    list_display = ('house', 'dormitory', 'session_type', 'date', 'conducted_by', 'status')
    list_filter = ('session_type', 'status', 'house', 'date')
    search_fields = ('house__name', 'dormitory__name', 'conducted_by__full_name')


@admin.register(RollCallEntry)
class RollCallEntryAdmin(admin.ModelAdmin):
    list_display = ('session', 'student', 'status', 'remarks')
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


admin.site.register(
    [
        Programme,
        House,
        Dormitory,
        ClassGroup,
        Guardian,
        AcademicYear,
        AcademicTerm,
    ]
)
