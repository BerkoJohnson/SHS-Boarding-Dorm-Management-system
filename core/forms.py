from uuid import UUID

from django import forms
from django.contrib.auth import get_user_model
from django.db import models
from django.db.models import Count, F, Q
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db import transaction
from .models import (
    DORMITORY_GENDER_BY_STUDENT_GENDER,
    DisputeCase,
    DormitoryCapacityException,
    Exeat,
    ExeatReconciliation,
    ExeatReview,
    GroupSMSCampaign,
    GateDutyAssignment,
    LateReturnIncident,
    OperationalSettings,
    RollCallCorrection,
    RollCallEntry,
    ScheduledReportEmail,
    StaffRole,
    Student,
    Programme,
    House,
    Dormitory,
    DormitoryRoom,
    ClassGroup,
    Guardian,
    HouseMaster,
    AcademicTerm,
    AcademicYear,
    DisciplinaryRecord,
    get_user_profile,
)
from .widgets import PrivateStudentPhotoInput

# Report types that are point-in-time snapshots. They ignore the date range and
# term filters entirely, so those controls are rendered disabled on their filter
# bars instead of silently accepting values that have no effect.
REPORTS_WITHOUT_PERIOD_FILTERS = frozenset(
    {
        "housing",
        "placement_exceptions",
        "room_register",
        "data_completeness",
    }
)

# Controls that only carry meaning for a period-scoped report.
PERIOD_FILTER_FIELDS = ("date_from", "date_to", "term")


def _uuid_or_none(value):
    try:
        return UUID(str(getattr(value, "pk", value)))
    except (TypeError, ValueError, AttributeError):
        return None


def available_dormitory_rooms(dormitory_id=None, include_room_id=None):
    """Return rooms that still have a free bed.

    Passing ``dormitory_id=None`` returns every available room; the create form
    uses that as a no-JavaScript fallback so the room control is never empty.
    """

    room_queryset = DormitoryRoom.objects.select_related(
        "dormitory", "dormitory__house"
    ).annotate(
        occupied=Count(
            "students",
            filter=Q(
                students__residency=Student.ResidencyStatus.BOARDING,
                students__status__in=(
                    Student.EnrollmentStatus.ENROLLED,
                    Student.EnrollmentStatus.APPROVED,
                    Student.EnrollmentStatus.IN_PROGRESS,
                ),
            ),
        )
    )
    available_rooms = Q(occupied__lt=F("capacity"))
    if include_room_id:
        available_rooms |= Q(pk=include_room_id)
    if dormitory_id:
        room_queryset = room_queryset.filter(dormitory_id=dormitory_id)
    return room_queryset.filter(available_rooms).order_by(
        "dormitory__house__display_order",
        "dormitory__house__name",
        "dormitory__name",
        "name",
    )


def report_uses_period_filters(report_type):
    """Return True when the given report type consumes the date/term filters."""
    return report_type not in REPORTS_WITHOUT_PERIOD_FILTERS


class ReportFilterForm(forms.Form):
    REPORT_CHOICES = (
        ("housing", "Boarding occupancy & placement"),
        ("attendance", "Roll-call attendance & follow-up"),
        ("exeats", "Exeat & gate compliance"),
        ("discipline", "Disciplinary trends"),
        ("placement_exceptions", "Placement exception queue"),
        ("attendance_league", "House attendance league"),
        ("overdue_register", "Overdue & late-return register"),
        ("gate_conflicts", "Gate / roll-call conflicts"),
        ("approval_turnaround", "Exeat approval turnaround"),
        ("exeat_analytics", "Exeat type & destination analytics"),
        ("room_register", "Room bed-allocation register"),
        ("staff_activity", "Staff activity & workload"),
        ("welfare_infirmary", "Welfare & infirmary referrals"),
        ("data_completeness", "Data completeness / register hygiene"),
        ("movement_concerns", "Movement concern reports"),
        ("conduct_normalized", "Conduct normalized by house size"),
        ("intervention_effectiveness", "Intervention effectiveness"),
    )

    report = forms.ChoiceField(
        choices=REPORT_CHOICES,
        required=False,
        label="Detailed report",
        widget=forms.Select(
            attrs={
                "class": "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm",
                "onchange": "this.form.submit()",
            }
        ),
    )
    date_from = forms.DateField(
        required=False,
        label="From",
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm",
            }
        ),
    )
    date_to = forms.DateField(
        required=False,
        label="To",
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm",
            }
        ),
    )
    term = forms.ModelChoiceField(
        queryset=AcademicTerm.objects.select_related("academic_year").order_by(
            "-start_date"
        ),
        required=False,
        label="Academic term",
        empty_label="All terms",
        widget=forms.Select(
            attrs={
                "class": "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
            }
        ),
    )

    def __init__(self, *args, disabled_fields=(), **kwargs):
        super().__init__(*args, **kwargs)
        for name in disabled_fields:
            field = self.fields.get(name)
            if field is None:
                continue
            field.disabled = True
            field.widget.attrs["disabled"] = "disabled"

    def clean(self):
        cleaned_data = super().clean()
        date_from = cleaned_data.get("date_from")
        date_to = cleaned_data.get("date_to")
        if date_from and date_to and date_from > date_to:
            self.add_error("date_to", "End date must be on or after the start date.")
        return cleaned_data


class MovementConcernReportForm(forms.Form):
    details = forms.CharField(
        max_length=1000,
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "maxlength": 1000,
                "autocomplete": "off",
            }
        ),
        label="What concerns you about this movement?",
        help_text="Do not include sensitive personal information.",
    )


class ExeatRequestForm(forms.ModelForm):
    """
    STEP 1 — Draft / Request form.

    Used by House Tutors, Housemasters and Admins to SUBMIT a new leave
    request on behalf of a student. The form always saves with status=PENDING
    (the user is never offered an approval option here). All authorization
    fields are removed — they are handled separately by ExeatApprovalForm
    during Step 2 (Housemaster review).

    Role who sees this: EXEAT_CREATE_ROLES (Admin, Senior HM, Housemaster, House Tutor).
    """

    class Meta:
        model = Exeat
        fields = [
            "student",
            "exeat_type",
            "expected_departure",
            "expected_return",
            "destination",
            "reason",
            "status",  # kept in fields but forced + disabled (Pending only)
        ]
        widgets = {
            "student": forms.HiddenInput(),
            "exeat_type": forms.Select(
                attrs={
                    "class": "w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-slate-50/80 focus:bg-white focus:outline-none focus:ring-1 focus:ring-brand-500 font-medium text-slate-800 transition"
                }
            ),
            "expected_departure": forms.DateTimeInput(
                attrs={
                    "type": "datetime-local",
                    "class": "w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-slate-50/80 focus:bg-white focus:outline-none focus:ring-1 focus:ring-brand-500 text-slate-800 transition",
                }
            ),
            "expected_return": forms.DateTimeInput(
                attrs={
                    "type": "datetime-local",
                    "class": "w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-slate-50/80 focus:bg-white focus:outline-none focus:ring-1 focus:ring-brand-500 text-slate-800 transition",
                }
            ),
            "destination": forms.TextInput(
                attrs={
                    "placeholder": "e.g. Korle-Bu Hospital, Accra / Guardian Residence, Kumasi",
                    "class": "w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-slate-50/80 focus:bg-white focus:outline-none focus:ring-1 focus:ring-brand-500 text-slate-800 placeholder-slate-400 transition",
                }
            ),
            "reason": forms.Textarea(
                attrs={
                    "rows": 3,
                    "placeholder": "Provide specific justification, clinic referral notes, or guardian authorization details...",
                    "class": "w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-slate-50/80 focus:bg-white focus:outline-none focus:ring-1 focus:ring-brand-500 text-slate-800 placeholder-slate-400 transition",
                }
            ),
            "status": forms.Select(
                attrs={
                    "class": "w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-slate-50/60 text-slate-600 font-medium transition cursor-not-allowed",
                    "disabled": "disabled",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["student"].queryset = Student.objects.exclude(
            status=Student.EnrollmentStatus.COMPLETED
        )

        # Force status to PENDING — this form cannot approve a pass.
        # Disabled UI + backend override in clean() guarantee this.
        self.fields["status"].choices = [
            (
                Exeat.Status.PENDING,
                Exeat.Status.PENDING.label + " — Housemaster Review Required",
            )
        ]
        self.initial["status"] = Exeat.Status.PENDING
        self.fields["status"].disabled = True
        self.fields["status"].required = False  # never user-facing, value is coerced

        # Default departure to now and return to tomorrow 17:00
        now = timezone.now()
        if not self.initial.get("expected_departure"):
            self.initial["expected_departure"] = now.strftime("%Y-%m-%dT%H:%M")
        if not self.initial.get("expected_return"):
            tomorrow_evening = (now + timezone.timedelta(days=1)).replace(
                hour=17, minute=0, second=0
            )
            self.initial["expected_return"] = tomorrow_evening.strftime(
                "%Y-%m-%dT%H:%M"
            )

    def clean(self):
        cleaned_data = super().clean()
        student = cleaned_data.get("student")
        departure = cleaned_data.get("expected_departure")
        expected_return = cleaned_data.get("expected_return")

        # 1. Enforce student residency rule
        if student and student.residency == Student.ResidencyStatus.DAY:
            raise ValidationError(
                {
                    "student": "Day students are not boarded and do not require exeat passes."
                }
            )
        if student and student.status == Student.EnrollmentStatus.COMPLETED:
            raise ValidationError(
                {"student": "Completed students cannot request new exeat passes."}
            )

        # 2. Date chronological validation
        if departure and expected_return:
            if expected_return <= departure:
                raise ValidationError(
                    {
                        "expected_return": "Expected return datetime must be after scheduled departure datetime."
                    }
                )

        # 3. Backend coercion: status MUST end up PENDING regardless of any POST tampering.
        cleaned_data["status"] = Exeat.Status.PENDING

        return cleaned_data


class ExeatReconciliationForm(forms.ModelForm):
    class Meta:
        model = ExeatReconciliation
        fields = ("outcome", "actual_departure", "actual_return", "reason")
        widgets = {
            "outcome": forms.Select(
                attrs={
                    "class": "w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                }
            ),
            "actual_departure": forms.DateTimeInput(
                attrs={
                    "type": "datetime-local",
                    "class": "w-full rounded-lg border border-slate-300 px-3 py-2 text-sm",
                }
            ),
            "actual_return": forms.DateTimeInput(
                attrs={
                    "type": "datetime-local",
                    "class": "w-full rounded-lg border border-slate-300 px-3 py-2 text-sm",
                }
            ),
            "reason": forms.Textarea(
                attrs={
                    "rows": 4,
                    "placeholder": "Record what was confirmed and how it was verified.",
                    "class": "w-full rounded-lg border border-slate-300 px-3 py-2 text-sm",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reason"].label = "Reconciliation notes / evidence"
        self.fields["reason"].required = True
        self.fields["actual_departure"].required = False
        self.fields["actual_return"].required = False

    def clean(self):
        cleaned_data = super().clean()
        outcome = cleaned_data.get("outcome")
        departure = cleaned_data.get("actual_departure")
        returned = cleaned_data.get("actual_return")

        if (
            outcome
            in (
                ExeatReconciliation.Outcome.STUDENT_OUT,
                ExeatReconciliation.Outcome.RETURNED,
            )
            and not departure
        ):
            self.add_error(
                "actual_departure", "Enter the confirmed or best-known departure time."
            )
        if outcome == ExeatReconciliation.Outcome.RETURNED and not returned:
            self.add_error(
                "actual_return", "Enter the confirmed or best-known return time."
            )
        if outcome == ExeatReconciliation.Outcome.UNUSED and (departure or returned):
            self.add_error(
                "actual_departure", "An unused pass cannot have travel timestamps."
            )
        if outcome != ExeatReconciliation.Outcome.RETURNED and returned:
            self.add_error(
                "actual_return",
                "A return time is only valid when the student has returned.",
            )
        if departure and returned and returned <= departure:
            self.add_error("actual_return", "Return time must be after departure time.")
        return cleaned_data


class ExeatApprovalForm(forms.ModelForm):
    """
    STEP 2 — Review form.

    Used ONLY by the student's assigned Housemaster, Senior Housemaster or an
    Admin to approve or reject a PENDING exeat request.

    Smart behaviour:
      * status choices restricted to [Approved, Rejected] only (Pending/Cancelled
        are handled elsewhere).
      * approved_by queryset is SCOPED: only the student's own Housemaster and the
        Senior Housemaster are shown as options.
      * If the current reviewing user is linked to a HouseMaster record and has
        jurisdiction, that record is auto-selected as the initial/default authorizer
        (so a Housemaster approving their own student never scrolls to pick themselves).
      * approval_remarks required on Reject, optional on Approve.
    """

    class Meta:
        model = Exeat
        fields = ["status", "approved_by", "approval_remarks"]
        widgets = {
            "status": forms.Select(
                attrs={
                    "class": "w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-slate-50/80 focus:bg-white focus:outline-none focus:ring-1 focus:ring-brand-500 font-medium text-slate-800 transition"
                }
            ),
            "approved_by": forms.Select(
                attrs={
                    "class": "w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-slate-50/80 focus:bg-white focus:outline-none focus:ring-1 focus:ring-brand-500 font-medium text-slate-800 transition"
                }
            ),
            "approval_remarks": forms.Textarea(
                attrs={
                    "rows": 2,
                    "placeholder": "If Approved: conditions (e.g. report back with hospital card, guardian escort required). "
                    "If Rejected: reason for refusal (required)...",
                    "class": "w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-slate-50/80 focus:bg-white focus:outline-none focus:ring-1 focus:ring-brand-500 text-slate-800 placeholder-slate-400 transition",
                }
            ),
        }

    def __init__(self, *args, student=None, reviewing_user=None, **kwargs):
        """
        Args:
            student: the Student this Exeat belongs to (required to scope authorizer dropdown).
            reviewing_user: the Django auth User currently reviewing the form (used for auto-select).
        """
        super().__init__(*args, **kwargs)

        # 1. Status choices: only Approve / Reject during review step.
        self.fields["status"].choices = [
            (Exeat.Status.APPROVED, Exeat.Status.APPROVED.label),
            (Exeat.Status.REJECTED, Exeat.Status.REJECTED.label),
        ]

        # 2. Scope the approving Housemaster dropdown to valid signatories only:
        #    * the student's own assigned Housemaster(s) for their house
        #    * the (one and only) Senior Housemaster (school-wide authority)
        eligible_qs = HouseMaster.objects.select_related("house")
        if student is not None:
            student_house_id = student.house_id if student.house_id else None
            eligible_qs = eligible_qs.filter(
                models.Q(is_senior_housemaster=True)
                | models.Q(house_id=student_house_id)
            )
        eligible_qs = eligible_qs.order_by("-is_senior_housemaster", "full_name")
        self.fields["approved_by"].queryset = eligible_qs

        # 3. Auto-select the current user as authorizer IF they are a HouseMaster
        #    record AND in the eligible list. Prevents HMs from manually hunting for
        #    their own name. (Admins and Senior HMs can still override if needed.)
        self.medical_authorizer = False
        if reviewing_user is not None:
            profile = get_user_profile(reviewing_user)
            self.medical_authorizer = bool(
                profile and profile.role == StaffRole.MEDICAL
            )
            if self.medical_authorizer:
                # The named medical lead has no HouseMaster row. Attribute the
                # decision to the authenticated user instead of inventing a
                # HouseMaster signatory, and hide the inapplicable dropdown.
                self.instance.approved_by_user = reviewing_user
                self.instance.approved_by = None
                self.fields["approved_by"].widget = forms.HiddenInput()
            hm = getattr(reviewing_user, "housemaster", None)
            if hm is None:
                # Backwards-compat fallback used elsewhere in the app
                hm = HouseMaster.objects.filter(
                    models.Q(staff_id__iexact=reviewing_user.username)
                    | models.Q(full_name__icontains=reviewing_user.username)
                ).first()
            if (
                hm is not None
                and not self.medical_authorizer
                and eligible_qs.filter(pk=hm.pk).exists()
            ):
                self.initial.setdefault("approved_by", hm.pk)

        # Required state enforced in clean(), not via field.required to give clear messages.
        self.fields["approved_by"].required = False
        self.fields["approval_remarks"].required = False

    def clean(self):
        cleaned_data = super().clean()
        status = cleaned_data.get("status")
        approved_by = cleaned_data.get("approved_by")
        remarks = (cleaned_data.get("approval_remarks") or "").strip()

        # 1. Approve → an authorizer is mandatory. Medical leads are attributed
        # through approved_by_user and have no HouseMaster row to select.
        if self.medical_authorizer:
            cleaned_data["approved_by"] = None
            approved_by = None
        if (
            status == Exeat.Status.APPROVED
            and not approved_by
            and not self.medical_authorizer
        ):
            raise ValidationError(
                {
                    "approved_by": "An authorizing Housemaster must be selected when approving a pass. "
                    "Pick your own record if you are the signing Housemaster."
                }
            )

        # 2. Reject → a reason MUST be captured in approval_remarks
        if status == Exeat.Status.REJECTED and not remarks:
            self.add_error(
                "approval_remarks",
                "Please explain the reason for rejection in Conditions / Special Remarks "
                "(e.g. insufficient notice, no guardian confirmation, midterm restriction).",
            )

        return cleaned_data


# Backwards compatibility alias — any existing references to ExeatCreateForm in
# templates, imports or tests continue to work and get the Step-1 request form.
ExeatCreateForm = ExeatRequestForm


class DisciplinaryRecordForm(forms.ModelForm):
    """
    Form for logging student conduct infractions and disciplinary actions.

    Includes styled Tailwind form inputs, severity controls, and validation.
    """

    class Meta:
        model = DisciplinaryRecord
        fields = [
            "student",
            "offense",
            "severity",
            "action_taken",
            "description",
        ]
        widgets = {
            "student": forms.HiddenInput(),
            "offense": forms.TextInput(
                attrs={
                    "placeholder": "e.g. Overstayed Exeat, Sneaking to Town, Missing Roll Call, Bullying",
                    "class": "w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-slate-50/80 focus:bg-white focus:outline-none focus:ring-1 focus:ring-brand-500 text-slate-800 placeholder-slate-400 transition",
                }
            ),
            "severity": forms.Select(
                attrs={
                    "class": "w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-slate-50/80 focus:bg-white focus:outline-none focus:ring-1 focus:ring-brand-500 font-medium text-slate-800 transition"
                }
            ),
            "action_taken": forms.TextInput(
                attrs={
                    "placeholder": "e.g. Gating for 1 weekend, campus grounds work, warning letter, suspension referral",
                    "class": "w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-slate-50/80 focus:bg-white focus:outline-none focus:ring-1 focus:ring-brand-500 text-slate-800 placeholder-slate-400 transition",
                }
            ),
            "description": forms.Textarea(
                attrs={
                    "rows": 3,
                    "placeholder": "Provide specific details regarding the incident, time/location observed, and any witness statements...",
                    "class": "w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-slate-50/80 focus:bg-white focus:outline-none focus:ring-1 focus:ring-brand-500 text-slate-800 placeholder-slate-400 transition",
                }
            ),
        }

    def clean(self):
        cleaned_data = super().clean()
        student = cleaned_data.get("student")
        offense = cleaned_data.get("offense")

        if not student:
            raise ValidationError(
                {"student": "Please select a student for this incident record."}
            )

        if not offense or not offense.strip():
            raise ValidationError(
                {"offense": "Please state the offense or infraction title."}
            )

        return cleaned_data


class StudentRegistrationForm(forms.ModelForm):
    guardian_name = forms.CharField(
        max_length=150,
        required=False,
        label="Parent / Guardian name",
        widget=forms.TextInput(attrs={"class": "student-form-control"}),
    )
    guardian_phone = forms.CharField(
        max_length=17,
        required=False,
        label="Parent / Guardian phone",
        widget=forms.TextInput(attrs={"class": "student-form-control"}),
    )
    guardian_alt_phone = forms.CharField(
        max_length=17,
        required=False,
        label="Alternative phone",
        widget=forms.TextInput(attrs={"class": "student-form-control"}),
    )

    class Meta:
        model = Student
        fields = (
            "admission_no",
            "index_number",
            "full_name",
            "gender",
            "date_of_birth",
            "programme",
            "class_group",
            "residency",
            "house",
            "dormitory",
            "room",
            "status",
            "photo",
        )
        widgets = {
            "admission_no": forms.TextInput(attrs={"class": "student-form-control"}),
            "index_number": forms.TextInput(attrs={"class": "student-form-control"}),
            "full_name": forms.TextInput(attrs={"class": "student-form-control"}),
            "gender": forms.Select(attrs={"class": "student-form-control"}),
            "date_of_birth": forms.DateInput(
                attrs={"type": "date", "class": "student-form-control"}
            ),
            "programme": forms.Select(attrs={"class": "student-form-control"}),
            "class_group": forms.Select(attrs={"class": "student-form-control"}),
            "residency": forms.Select(attrs={"class": "student-form-control"}),
            "house": forms.Select(attrs={"class": "student-form-control"}),
            "dormitory": forms.Select(attrs={"class": "student-form-control"}),
            "room": forms.Select(attrs={"class": "student-form-control"}),
            "status": forms.Select(attrs={"class": "student-form-control"}),
            "photo": PrivateStudentPhotoInput(
                attrs={
                    "accept": "image/jpeg,image/png",
                    "class": "student-form-control",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["programme"].queryset = Programme.objects.order_by("name")
        selected_programme = (
            self.data.get("programme")
            if self.is_bound
            else self.initial.get("programme")
            or getattr(self.instance, "programme_id", None)
        )
        selected_programme = _uuid_or_none(selected_programme)
        # A brand-new, unbound form renders outside the browser's JavaScript
        # flow too. Offer everything in that case; the bound form always applies
        # the parent filters below, so server-side validation is unchanged.
        # BaseModel assigns a UUID at construction, so ``_state.adding`` is the
        # reliable way to tell a new record from one loaded from the database.
        is_new_create_form = self.instance._state.adding and not self.is_bound
        if selected_programme:
            # Mirror Student.clean(): a class is valid when it belongs to the
            # selected programme or carries no programme (ClassGroup.programme
            # is nullable and set to NULL when a programme is deleted). The
            # instance's current class is always kept so editing a student can
            # never silently clear it.
            class_group_filter = Q(programme__isnull=True) | Q(
                programme_id=selected_programme
            )
            if self.instance and self.instance.pk and self.instance.class_group_id:
                class_group_filter |= Q(pk=self.instance.class_group_id)
            self.fields["class_group"].queryset = ClassGroup.objects.filter(
                class_group_filter
            ).order_by("name")
        elif is_new_create_form:
            self.fields["class_group"].queryset = ClassGroup.objects.order_by("name")
        else:
            self.fields["class_group"].queryset = ClassGroup.objects.none()
        self.fields["house"].queryset = House.objects.order_by("display_order", "name")
        selected_dormitory = (
            self.data.get("dormitory")
            if self.is_bound
            else self.initial.get("dormitory")
            or getattr(self.instance, "dormitory_id", None)
        )
        selected_house = (
            self.data.get("house")
            if self.is_bound
            else self.initial.get("house") or getattr(self.instance, "house_id", None)
        )
        selected_gender = (
            self.data.get("gender")
            if self.is_bound
            else self.initial.get("gender") or getattr(self.instance, "gender", None)
        )
        selected_dormitory = _uuid_or_none(selected_dormitory)
        selected_house = _uuid_or_none(selected_house)
        dormitory_queryset = Dormitory.objects.select_related("house")
        if selected_house:
            dormitory_queryset = dormitory_queryset.filter(house_id=selected_house)
        elif not is_new_create_form:
            dormitory_queryset = dormitory_queryset.none()
        dormitory_gender = DORMITORY_GENDER_BY_STUDENT_GENDER.get(selected_gender)
        if dormitory_gender:
            gender_filter = Q(gender=dormitory_gender)
            # Keep the record's current dormitory selectable so editing a legacy
            # or imported gender mismatch surfaces the model's validation error
            # instead of silently clearing the placement. Only retain it while it
            # still belongs to the selected house.
            if (
                not self.instance._state.adding
                and self.instance.dormitory_id
                and (not selected_house or self.instance.house_id == selected_house)
            ):
                gender_filter |= Q(pk=self.instance.dormitory_id)
            dormitory_queryset = dormitory_queryset.filter(gender_filter)
        self.fields["dormitory"].queryset = dormitory_queryset.order_by(
            "house__display_order", "house__name", "name"
        )
        if selected_dormitory:
            room_queryset = available_dormitory_rooms(
                selected_dormitory,
                include_room_id=(
                    self.instance.room_id
                    if self.instance and self.instance.pk
                    else None
                ),
            )
        elif is_new_create_form:
            # No-JavaScript fallback: show every room that still has a bed.
            room_queryset = available_dormitory_rooms()
        else:
            room_queryset = DormitoryRoom.objects.none()
        self.fields["room"].queryset = room_queryset
        # Lock the class selector only when editing a stored record; a new form
        # must let the user choose a class. BaseModel assigns a UUID at
        # construction, so ``_state.adding`` (not ``pk``) marks a new record.
        if not self.instance._state.adding:
            self.fields["class_group"].disabled = True
        if self.instance.status == Student.EnrollmentStatus.COMPLETED:
            self.fields["status"].disabled = True
        else:
            self.fields["status"].choices = [
                choice
                for choice in self.fields["status"].choices
                if choice[0] != Student.EnrollmentStatus.COMPLETED
            ]
        self.fields["class_group"].required = False
        self.fields["house"].required = False
        self.fields["dormitory"].required = False
        self.fields["room"].required = False

        if self.instance and self.instance.pk and self.instance.guardian_id:
            guardian = self.instance.guardian
            self.initial.update(
                {
                    "guardian_name": guardian.full_name,
                    "guardian_phone": guardian.phone_number,
                    "guardian_alt_phone": guardian.alt_phone_number or "",
                }
            )

    def clean(self):
        cleaned_data = super().clean()
        residency = cleaned_data.get("residency")
        dormitory = cleaned_data.get("dormitory")
        room = cleaned_data.get("room")
        if (
            residency == Student.ResidencyStatus.BOARDING
            and dormitory
            and DormitoryRoom.objects.filter(dormitory=dormitory).exists()
            and not room
        ):
            self.add_error(
                "room",
                "Assign this boarding student to a room in the selected dormitory.",
            )
        for field_name in ("admission_no", "index_number"):
            value = (cleaned_data.get(field_name) or "").strip()
            matches = Student.objects.filter(**{f"{field_name}__iexact": value})
            if self.instance.pk:
                matches = matches.exclude(pk=self.instance.pk)
            if value and matches.exists():
                self.add_error(
                    field_name,
                    "This identifier is already assigned to another student.",
                )
        guardian_name = (cleaned_data.get("guardian_name") or "").strip()
        guardian_phone = (cleaned_data.get("guardian_phone") or "").strip()
        guardian_alt_phone = (cleaned_data.get("guardian_alt_phone") or "").strip()

        if guardian_name and not guardian_phone:
            self.add_error(
                "guardian_phone", "Enter a contact number for this guardian."
            )
        if guardian_phone and not guardian_name:
            self.add_error("guardian_name", "Enter the guardian's name.")
        if guardian_alt_phone and not guardian_phone:
            self.add_error(
                "guardian_phone", "Enter the primary contact before an alternative."
            )
        if guardian_phone:
            try:
                Guardian.phone_regex(guardian_phone)
            except ValidationError as error:
                self.add_error("guardian_phone", error)
        if guardian_alt_phone:
            try:
                Guardian.phone_regex(guardian_alt_phone)
            except ValidationError as error:
                self.add_error("guardian_alt_phone", error)
        if guardian_phone:
            matching_guardians = Guardian.objects.filter(
                phone_number=guardian_phone
            ).exclude(pk=self.instance.guardian_id if self.instance.pk else None)
            if (
                matching_guardians.exists()
                and not matching_guardians.filter(
                    full_name__iexact=guardian_name
                ).exists()
            ):
                self.add_error(
                    "guardian_phone",
                    "This phone number is already linked to a different guardian.",
                )
        return cleaned_data

    def save(self, commit=True):
        student = super().save(commit=False)
        if not commit:
            return student

        guardian_name = self.cleaned_data.get("guardian_name", "").strip()
        guardian_phone = self.cleaned_data.get("guardian_phone", "").strip()
        guardian_alt_phone = self.cleaned_data.get("guardian_alt_phone", "").strip()
        with transaction.atomic():
            if guardian_name and guardian_phone:
                guardian = Guardian.objects.filter(
                    phone_number=guardian_phone,
                    full_name__iexact=guardian_name,
                ).first()
                if guardian is None:
                    guardian = Guardian.objects.create(
                        full_name=guardian_name,
                        phone_number=guardian_phone,
                        alt_phone_number=guardian_alt_phone or None,
                    )
                elif guardian.alt_phone_number != (guardian_alt_phone or None):
                    guardian.alt_phone_number = guardian_alt_phone or None
                    guardian.save(update_fields=["alt_phone_number", "updated_at"])
                student.guardian = guardian
            else:
                student.guardian = None
            student.save()
            self.save_m2m()
        return student


class StudentPromotionForm(forms.Form):
    academic_year = forms.ModelChoiceField(
        queryset=AcademicYear.objects.order_by("-start_date"),
        label="Academic year students are entering",
        widget=forms.Select(
            attrs={
                "class": "min-h-11 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-base text-slate-800",
            }
        ),
    )
    source_class = forms.ModelChoiceField(
        queryset=ClassGroup.objects.order_by("name"),
        label="Current class",
        widget=forms.Select(
            attrs={
                "class": "min-h-11 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-base text-slate-800",
            }
        ),
    )
    target_class = forms.ModelChoiceField(
        queryset=ClassGroup.objects.order_by("name"),
        label="Promote to class",
        widget=forms.Select(
            attrs={
                "class": "min-h-11 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-base text-slate-800",
            }
        ),
    )

    def clean(self):
        cleaned_data = super().clean()
        source = cleaned_data.get("source_class")
        target = cleaned_data.get("target_class")
        if source and target:
            from core.services.student_progression import class_group_level

            source_level = class_group_level(source)
            target_level = class_group_level(target)
            if source_level not in {1, 2} or target_level != source_level + 1:
                raise ValidationError(
                    "Choose consecutive SHS classes, such as SHS 1 to SHS 2."
                )
        academic_year = cleaned_data.get("academic_year")
        if academic_year and academic_year.end_date < timezone.localdate():
            self.add_error(
                "academic_year",
                "Choose an academic year that has not ended.",
            )
        return cleaned_data


class StudentRosterUploadForm(forms.Form):
    roster_file = forms.FileField(
        label="Student roster file",
        widget=forms.ClearableFileInput(
            attrs={
                "accept": ".csv,.xlsx",
                "class": "student-form-control",
            }
        ),
    )

    def clean_roster_file(self):
        roster_file = self.cleaned_data["roster_file"]
        extension = roster_file.name.rsplit(".", 1)[-1].lower()
        if extension not in {"csv", "xlsx"}:
            raise ValidationError("Upload a CSV or Excel .xlsx file.")
        if roster_file.size > 10 * 1024 * 1024:
            raise ValidationError("Roster files must be 10 MB or smaller.")
        return roster_file


# ---------------------------------------------------------------------------
# Full P1 operational workflows
# ---------------------------------------------------------------------------

_INPUT_CLASS = (
    "w-full rounded-lg border border-slate-300 px-3 py-2 text-sm "
    "focus:outline-none focus:ring-2 focus:ring-brand-500"
)
_TEXTAREA_CLASS = _INPUT_CLASS


class ExeatRescheduleForm(forms.Form):
    expected_departure = forms.DateTimeField(
        label="New departure",
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local", "class": _INPUT_CLASS}
        ),
    )
    expected_return = forms.DateTimeField(
        label="New return",
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local", "class": _INPUT_CLASS}
        ),
    )
    reason = forms.CharField(
        label="Reason for the schedule change",
        max_length=2000,
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "class": _TEXTAREA_CLASS,
                "placeholder": "Explain the change and who authorized it.",
            }
        ),
    )

    def __init__(self, *args, exeat=None, **kwargs):
        self.exeat = exeat
        super().__init__(*args, **kwargs)
        if exeat is not None and not self.is_bound:
            self.initial.setdefault(
                "expected_departure",
                timezone.localtime(exeat.expected_departure).strftime("%Y-%m-%dT%H:%M"),
            )
            self.initial.setdefault(
                "expected_return",
                timezone.localtime(exeat.expected_return).strftime("%Y-%m-%dT%H:%M"),
            )

    def clean(self):
        cleaned = super().clean()
        departure = cleaned.get("expected_departure")
        returned = cleaned.get("expected_return")
        if departure and returned and returned <= departure:
            raise ValidationError(
                {"expected_return": "The new return time must be after departure."}
            )
        return cleaned


class EmergencyOverrideForm(forms.Form):
    reason = forms.CharField(
        label="Immediate reason and authority",
        min_length=10,
        max_length=2000,
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "class": _TEXTAREA_CLASS,
                "placeholder": (
                    "State the emergency, who authorized the movement, and why "
                    "the normal approval path could not be completed."
                ),
            }
        ),
    )


class ExeatReviewDecisionForm(forms.Form):
    outcome = forms.ChoiceField(
        choices=ExeatReview.Outcome.choices,
        widget=forms.Select(attrs={"class": _INPUT_CLASS}),
    )
    review_notes = forms.CharField(
        label="Review notes",
        required=True,
        max_length=4000,
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "class": _TEXTAREA_CLASS,
                "placeholder": "Record the evidence reviewed and any follow-up action.",
            }
        ),
    )


class GateDutyAssignmentForm(forms.ModelForm):
    class Meta:
        model = GateDutyAssignment
        fields = ("user", "gate_name", "starts_at", "ends_at", "is_temporary", "reason")
        widgets = {
            "user": forms.Select(attrs={"class": _INPUT_CLASS}),
            "gate_name": forms.TextInput(attrs={"class": _INPUT_CLASS}),
            "starts_at": forms.DateTimeInput(
                attrs={"type": "datetime-local", "class": _INPUT_CLASS}
            ),
            "ends_at": forms.DateTimeInput(
                attrs={"type": "datetime-local", "class": _INPUT_CLASS}
            ),
            "is_temporary": forms.CheckboxInput(
                attrs={"class": "h-4 w-4 rounded border-slate-300"}
            ),
            "reason": forms.Textarea(attrs={"rows": 2, "class": _TEXTAREA_CLASS}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        User = get_user_model()
        self.fields["user"].queryset = User.objects.filter(
            is_active=True,
            is_staff=True,
            profile__role__in=(StaffRole.ADMIN, StaffRole.GATE_OFFICER),
        ).order_by("first_name", "last_name", "username")


class LateReturnIncidentResolutionForm(forms.ModelForm):
    class Meta:
        model = LateReturnIncident
        fields = ("status", "resolution")
        widgets = {
            "status": forms.Select(attrs={"class": _INPUT_CLASS}),
            "resolution": forms.Textarea(
                attrs={
                    "rows": 3,
                    "class": _TEXTAREA_CLASS,
                    "placeholder": "Record the investigation outcome and any action taken.",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["status"].choices = [
            (LateReturnIncident.Status.INVESTIGATING, "Investigating"),
            (LateReturnIncident.Status.RESOLVED, "Resolved"),
            (LateReturnIncident.Status.CLOSED, "Closed"),
        ]

    def clean_resolution(self):
        value = (self.cleaned_data.get("resolution") or "").strip()
        if not value:
            raise ValidationError("Record the investigation outcome.")
        return value


class RollCallCorrectionRequestForm(forms.Form):
    requested_status = forms.ChoiceField(
        choices=RollCallEntry.Status.choices,
        widget=forms.Select(attrs={"class": _INPUT_CLASS}),
    )
    requested_remarks = forms.CharField(
        label="Corrected remarks",
        required=False,
        max_length=200,
        widget=forms.TextInput(attrs={"class": _INPUT_CLASS}),
    )
    reason = forms.CharField(
        label="Reason for the correction",
        max_length=2000,
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "class": _TEXTAREA_CLASS,
                "placeholder": "Explain why the original mark was wrong.",
            }
        ),
    )
    evidence = forms.CharField(
        label="Supporting evidence",
        required=False,
        max_length=4000,
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "class": _TEXTAREA_CLASS,
                "placeholder": (
                    "Required for Present/Absent or conduct-linked changes. "
                    "Record what was checked, not sensitive medical detail."
                ),
            }
        ),
    )

    def __init__(self, *args, entry=None, **kwargs):
        self.entry = entry
        super().__init__(*args, **kwargs)
        if entry is not None:
            self.fields["requested_status"].choices = [
                choice
                for choice in RollCallEntry.Status.choices
                if choice[0] != entry.status
            ]


class DormitoryCapacityExceptionForm(forms.ModelForm):
    class Meta:
        model = DormitoryCapacityException
        fields = (
            "dormitory",
            "starts_at",
            "ends_at",
            "additional_capacity",
            "reason",
        )
        widgets = {
            "dormitory": forms.Select(attrs={"class": _INPUT_CLASS}),
            "starts_at": forms.DateTimeInput(
                attrs={"type": "datetime-local", "class": _INPUT_CLASS}
            ),
            "ends_at": forms.DateTimeInput(
                attrs={"type": "datetime-local", "class": _INPUT_CLASS}
            ),
            "additional_capacity": forms.NumberInput(
                attrs={"min": 1, "max": 100, "class": _INPUT_CLASS}
            ),
            "reason": forms.Textarea(attrs={"rows": 3, "class": _TEXTAREA_CLASS}),
        }


class OperationalSettingsForm(forms.ModelForm):
    class Meta:
        model = OperationalSettings
        fields = (
            "roll_call_timeout_minutes",
            "late_return_incident_after_minutes",
            "senior_housemaster_escalation_after_minutes",
            "headmaster_escalation_after_minutes",
            "absence_follow_up_deadline_hours",
            "gate_duty_enforcement_enabled",
        )
        widgets = {
            "roll_call_timeout_minutes": forms.NumberInput(
                attrs={"class": _INPUT_CLASS, "min": 5, "max": 240}
            ),
            "late_return_incident_after_minutes": forms.NumberInput(
                attrs={"class": _INPUT_CLASS, "min": 15, "max": 1440}
            ),
            "senior_housemaster_escalation_after_minutes": forms.NumberInput(
                attrs={"class": _INPUT_CLASS, "min": 15, "max": 1440}
            ),
            "headmaster_escalation_after_minutes": forms.NumberInput(
                attrs={"class": _INPUT_CLASS, "min": 30, "max": 2880}
            ),
            "absence_follow_up_deadline_hours": forms.NumberInput(
                attrs={"class": _INPUT_CLASS, "min": 1, "max": 168}
            ),
            "gate_duty_enforcement_enabled": forms.CheckboxInput(
                attrs={"class": "h-4 w-4 rounded border-slate-300"}
            ),
        }


class DisputeCaseForm(forms.ModelForm):
    class Meta:
        model = DisputeCase
        fields = ("student", "category", "exeat", "roll_call_entry", "details")
        widgets = {
            "student": forms.Select(attrs={"class": _INPUT_CLASS}),
            "category": forms.Select(attrs={"class": _INPUT_CLASS}),
            "exeat": forms.Select(attrs={"class": _INPUT_CLASS}),
            "roll_call_entry": forms.Select(attrs={"class": _INPUT_CLASS}),
            "details": forms.Textarea(
                attrs={
                    "rows": 5,
                    "class": _TEXTAREA_CLASS,
                    "placeholder": (
                        "Record exactly what is disputed, the evidence relied on, "
                        "and the outcome the student or guardian is seeking."
                    ),
                }
            ),
        }

    def clean(self):
        cleaned = super().clean()
        student = cleaned.get("student")
        exeat = cleaned.get("exeat")
        entry = cleaned.get("roll_call_entry")
        if student and exeat and exeat.student_id != student.pk:
            raise ValidationError(
                {"exeat": "This pass belongs to a different student."}
            )
        if student and entry and entry.student_id != student.pk:
            raise ValidationError(
                {
                    "roll_call_entry": "This roll-call entry belongs to a different student."
                }
            )
        return cleaned


class DisputeDecisionForm(forms.Form):
    status = forms.ChoiceField(
        choices=(
            (DisputeCase.Status.UNDER_REVIEW, "Under review"),
            (DisputeCase.Status.RESOLVED, "Resolved / upheld"),
            (DisputeCase.Status.REJECTED, "Not upheld"),
            (DisputeCase.Status.CLOSED, "Closed"),
        ),
        widget=forms.Select(attrs={"class": _INPUT_CLASS}),
    )
    outcome = forms.ChoiceField(
        choices=(("", "Select when deciding"),) + tuple(DisputeCase.Outcome.choices),
        required=False,
        widget=forms.Select(attrs={"class": _INPUT_CLASS}),
    )
    assigned_to = forms.ModelChoiceField(
        label="Assign to",
        queryset=None,
        required=False,
        widget=forms.Select(attrs={"class": _INPUT_CLASS}),
    )
    decision_notes = forms.CharField(
        label="Review notes / decision reasoning",
        required=False,
        max_length=4000,
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "class": _TEXTAREA_CLASS,
                "placeholder": "Record what was reviewed and why the decision is fair.",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        User = get_user_model()
        self.fields["assigned_to"].queryset = User.objects.filter(
            is_active=True,
            is_staff=True,
        ).order_by("first_name", "last_name", "username")

    def clean(self):
        cleaned = super().clean()
        status = cleaned.get("status")
        if status and status != DisputeCase.Status.UNDER_REVIEW:
            if not cleaned.get("outcome"):
                raise ValidationError(
                    {"outcome": "Choose an outcome when deciding the dispute."}
                )
            if not (cleaned.get("decision_notes") or "").strip():
                raise ValidationError(
                    {"decision_notes": "Record the reasoning for the decision."}
                )
        return cleaned


class GroupSMSForm(forms.Form):
    scope = forms.ChoiceField(
        label="Recipient group",
        choices=GroupSMSCampaign.Scope.choices,
        widget=forms.Select(attrs={"class": _INPUT_CLASS}),
    )
    house = forms.ModelChoiceField(
        label="House",
        queryset=House.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": _INPUT_CLASS}),
    )
    message = forms.CharField(
        label="Message",
        max_length=500,
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "class": _TEXTAREA_CLASS,
                "placeholder": "Write the approved message for guardians.",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["house"].queryset = House.objects.order_by("display_order", "name")

    def clean(self):
        cleaned = super().clean()
        scope = cleaned.get("scope")
        house = cleaned.get("house")
        if scope == GroupSMSCampaign.Scope.HOUSE and not house:
            self.add_error("house", "Choose a house for this recipient group.")
        if scope == GroupSMSCampaign.Scope.ALL_BOARDERS:
            cleaned["house"] = None
        cleaned["message"] = (cleaned.get("message") or "").strip()
        return cleaned


class ScheduledReportEmailForm(forms.Form):
    report_type = forms.ChoiceField(
        label="Report",
        choices=ScheduledReportEmail.ReportType.choices,
        widget=forms.Select(attrs={"class": _INPUT_CLASS}),
    )
    frequency = forms.ChoiceField(
        choices=ScheduledReportEmail.Frequency.choices,
        widget=forms.Select(attrs={"class": _INPUT_CLASS}),
    )
    weekday = forms.ChoiceField(
        choices=(("", "Select a weekday"),)
        + tuple(ScheduledReportEmail.Weekday.choices),
        required=False,
        initial=ScheduledReportEmail.Weekday.MONDAY,
        widget=forms.Select(attrs={"class": _INPUT_CLASS}),
    )
    send_at = forms.TimeField(
        label="Delivery time",
        initial="07:00",
        input_formats=["%H:%M"],
        widget=forms.TimeInput(
            format="%H:%M",
            attrs={"type": "time", "class": _INPUT_CLASS},
        ),
    )
    file_format = forms.ChoiceField(
        label="Attachment format",
        choices=ScheduledReportEmail.FileFormat.choices,
        widget=forms.Select(attrs={"class": _INPUT_CLASS}),
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get(
            "frequency"
        ) == ScheduledReportEmail.Frequency.WEEKLY and not cleaned.get("weekday"):
            self.add_error("weekday", "Choose a weekday for weekly delivery.")
        if cleaned.get("frequency") == ScheduledReportEmail.Frequency.DAILY:
            cleaned["weekday"] = ScheduledReportEmail.Weekday.MONDAY
        return cleaned
