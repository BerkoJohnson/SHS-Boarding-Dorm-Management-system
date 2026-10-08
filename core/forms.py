from django import forms
from django.db import models
from django.utils import timezone
from django.core.exceptions import ValidationError
from .models import (
    Exeat,
    ExeatReconciliation,
    Student,
    HouseMaster,
    AcademicTerm,
    DisciplinaryRecord,
)


class ReportFilterForm(forms.Form):
    REPORT_CHOICES = (
        ("housing", "Boarding occupancy & placement"),
        ("attendance", "Roll-call attendance & follow-up"),
        ("exeats", "Exeat & gate compliance"),
        ("discipline", "Disciplinary trends"),
    )

    report = forms.ChoiceField(
        choices=REPORT_CHOICES,
        required=False,
        widget=forms.Select(attrs={
            "class": "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
        }),
    )
    date_from = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={
            "type": "date",
            "class": "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm",
        }),
    )
    date_to = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={
            "type": "date",
            "class": "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm",
        }),
    )
    term = forms.ModelChoiceField(
        queryset=AcademicTerm.objects.select_related("academic_year").order_by("-start_date"),
        required=False,
        empty_label="All terms",
        widget=forms.Select(attrs={
            "class": "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
        }),
    )

    def clean(self):
        cleaned_data = super().clean()
        date_from = cleaned_data.get("date_from")
        date_to = cleaned_data.get("date_to")
        if date_from and date_to and date_from > date_to:
            self.add_error("date_to", "End date must be on or after the start date.")
        return cleaned_data


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
            'student',
            'exeat_type',
            'expected_departure',
            'expected_return',
            'destination',
            'reason',
            'status',   # kept in fields but forced + disabled (Pending only)
        ]
        widgets = {
            'student': forms.HiddenInput(),
            'exeat_type': forms.Select(attrs={
                'class': 'w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-slate-50/80 focus:bg-white focus:outline-none focus:ring-1 focus:ring-brand-500 font-medium text-slate-800 transition'
            }),
            'expected_departure': forms.DateTimeInput(attrs={
                'type': 'datetime-local',
                'class': 'w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-slate-50/80 focus:bg-white focus:outline-none focus:ring-1 focus:ring-brand-500 text-slate-800 transition'
            }),
            'expected_return': forms.DateTimeInput(attrs={
                'type': 'datetime-local',
                'class': 'w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-slate-50/80 focus:bg-white focus:outline-none focus:ring-1 focus:ring-brand-500 text-slate-800 transition'
            }),
            'destination': forms.TextInput(attrs={
                'placeholder': 'e.g. Korle-Bu Hospital, Accra / Guardian Residence, Kumasi',
                'class': 'w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-slate-50/80 focus:bg-white focus:outline-none focus:ring-1 focus:ring-brand-500 text-slate-800 placeholder-slate-400 transition'
            }),
            'reason': forms.Textarea(attrs={
                'rows': 3,
                'placeholder': 'Provide specific justification, clinic referral notes, or guardian authorization details...',
                'class': 'w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-slate-50/80 focus:bg-white focus:outline-none focus:ring-1 focus:ring-brand-500 text-slate-800 placeholder-slate-400 transition'
            }),
            'status': forms.Select(attrs={
                'class': 'w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-slate-50/60 text-slate-600 font-medium transition cursor-not-allowed',
                'disabled': 'disabled',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Force status to PENDING — this form cannot approve a pass.
        # Disabled UI + backend override in clean() guarantee this.
        self.fields['status'].choices = [(Exeat.Status.PENDING, Exeat.Status.PENDING.label + ' — Housemaster Review Required')]
        self.initial['status'] = Exeat.Status.PENDING
        self.fields['status'].disabled = True
        self.fields['status'].required = False  # never user-facing, value is coerced

        # Default departure to now and return to tomorrow 17:00
        now = timezone.now()
        if not self.initial.get('expected_departure'):
            self.initial['expected_departure'] = now.strftime('%Y-%m-%dT%H:%M')
        if not self.initial.get('expected_return'):
            tomorrow_evening = (now + timezone.timedelta(days=1)).replace(hour=17, minute=0, second=0)
            self.initial['expected_return'] = tomorrow_evening.strftime('%Y-%m-%dT%H:%M')

    def clean(self):
        cleaned_data = super().clean()
        student = cleaned_data.get('student')
        departure = cleaned_data.get('expected_departure')
        expected_return = cleaned_data.get('expected_return')

        # 1. Enforce student residency rule
        if student and student.residency == Student.ResidencyStatus.DAY:
            raise ValidationError({'student': "Day students are not boarded and do not require exeat passes."})

        # 2. Date chronological validation
        if departure and expected_return:
            if expected_return <= departure:
                raise ValidationError({
                    'expected_return': "Expected return datetime must be after scheduled departure datetime."
                })

        # 3. Backend coercion: status MUST end up PENDING regardless of any POST tampering.
        cleaned_data['status'] = Exeat.Status.PENDING

        return cleaned_data


class ExeatReconciliationForm(forms.ModelForm):
    class Meta:
        model = ExeatReconciliation
        fields = ("outcome", "actual_departure", "actual_return", "reason")
        widgets = {
            "outcome": forms.Select(attrs={"class": "w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"}),
            "actual_departure": forms.DateTimeInput(
                attrs={"type": "datetime-local", "class": "w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"}
            ),
            "actual_return": forms.DateTimeInput(
                attrs={"type": "datetime-local", "class": "w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"}
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

        if outcome in (ExeatReconciliation.Outcome.STUDENT_OUT, ExeatReconciliation.Outcome.RETURNED) and not departure:
            self.add_error("actual_departure", "Enter the confirmed or best-known departure time.")
        if outcome == ExeatReconciliation.Outcome.RETURNED and not returned:
            self.add_error("actual_return", "Enter the confirmed or best-known return time.")
        if outcome == ExeatReconciliation.Outcome.UNUSED and (departure or returned):
            self.add_error("actual_departure", "An unused pass cannot have travel timestamps.")
        if outcome != ExeatReconciliation.Outcome.RETURNED and returned:
            self.add_error("actual_return", "A return time is only valid when the student has returned.")
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
        fields = ['status', 'approved_by', 'approval_remarks']
        widgets = {
            'status': forms.Select(attrs={
                'class': 'w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-slate-50/80 focus:bg-white focus:outline-none focus:ring-1 focus:ring-brand-500 font-medium text-slate-800 transition'
            }),
            'approved_by': forms.Select(attrs={
                'class': 'w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-slate-50/80 focus:bg-white focus:outline-none focus:ring-1 focus:ring-brand-500 font-medium text-slate-800 transition'
            }),
            'approval_remarks': forms.Textarea(attrs={
                'rows': 2,
                'placeholder': 'If Approved: conditions (e.g. report back with hospital card, guardian escort required). '  
                               'If Rejected: reason for refusal (required)...',
                'class': 'w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-slate-50/80 focus:bg-white focus:outline-none focus:ring-1 focus:ring-brand-500 text-slate-800 placeholder-slate-400 transition'
            }),
        }

    def __init__(self, *args, student=None, reviewing_user=None, **kwargs):
        """
        Args:
            student: the Student this Exeat belongs to (required to scope authorizer dropdown).
            reviewing_user: the Django auth User currently reviewing the form (used for auto-select).
        """
        super().__init__(*args, **kwargs)

        # 1. Status choices: only Approve / Reject during review step.
        self.fields['status'].choices = [
            (Exeat.Status.APPROVED, Exeat.Status.APPROVED.label),
            (Exeat.Status.REJECTED, Exeat.Status.REJECTED.label),
        ]

        # 2. Scope the approving Housemaster dropdown to valid signatories only:
        #    * the student's own assigned Housemaster(s) for their house
        #    * the (one and only) Senior Housemaster (school-wide authority)
        eligible_qs = HouseMaster.objects.select_related('house')
        if student is not None:
            student_house_id = student.house_id if student.house_id else None
            eligible_qs = eligible_qs.filter(
                models.Q(is_senior_housemaster=True)
                | models.Q(house_id=student_house_id)
            )
        eligible_qs = eligible_qs.order_by('-is_senior_housemaster', 'full_name')
        self.fields['approved_by'].queryset = eligible_qs

        # 3. Auto-select the current user as authorizer IF they are a HouseMaster
        #    record AND in the eligible list. Prevents HMs from manually hunting for
        #    their own name. (Admins and Senior HMs can still override if needed.)
        if reviewing_user is not None:
            hm = getattr(reviewing_user, 'housemaster', None)
            if hm is None:
                # Backwards-compat fallback used elsewhere in the app
                hm = HouseMaster.objects.filter(
                    models.Q(staff_id__iexact=reviewing_user.username)
                    | models.Q(full_name__icontains=reviewing_user.username)
                ).first()
            if hm is not None and eligible_qs.filter(pk=hm.pk).exists():
                self.initial.setdefault('approved_by', hm.pk)

        # Required state enforced in clean(), not via field.required to give clear messages.
        self.fields['approved_by'].required = False
        self.fields['approval_remarks'].required = False

    def clean(self):
        cleaned_data = super().clean()
        status = cleaned_data.get('status')
        approved_by = cleaned_data.get('approved_by')
        remarks = (cleaned_data.get('approval_remarks') or '').strip()

        # 1. Approve → authorizer is mandatory (model-level Exeat.clean also enforces this)
        if status == Exeat.Status.APPROVED and not approved_by:
            raise ValidationError({
                'approved_by': "An authorizing Housemaster must be selected when approving a pass. "  
                               "Pick your own record if you are the signing Housemaster."
            })

        # 2. Reject → a reason MUST be captured in approval_remarks
        if status == Exeat.Status.REJECTED and not remarks:
            self.add_error(
                'approval_remarks',
                "Please explain the reason for rejection in Conditions / Special Remarks "  
                "(e.g. insufficient notice, no guardian confirmation, midterm restriction)."
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
            'student',
            'offense',
            'severity',
            'action_taken',
            'description',
        ]
        widgets = {
            'student': forms.HiddenInput(),
            'offense': forms.TextInput(attrs={
                'placeholder': 'e.g. Overstayed Exeat, Sneaking to Town, Missing Roll Call, Bullying',
                'class': 'w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-slate-50/80 focus:bg-white focus:outline-none focus:ring-1 focus:ring-brand-500 text-slate-800 placeholder-slate-400 transition'
            }),
            'severity': forms.Select(attrs={
                'class': 'w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-slate-50/80 focus:bg-white focus:outline-none focus:ring-1 focus:ring-brand-500 font-medium text-slate-800 transition'
            }),
            'action_taken': forms.TextInput(attrs={
                'placeholder': 'e.g. Gating for 1 weekend, campus grounds work, warning letter, suspension referral',
                'class': 'w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-slate-50/80 focus:bg-white focus:outline-none focus:ring-1 focus:ring-brand-500 text-slate-800 placeholder-slate-400 transition'
            }),
            'description': forms.Textarea(attrs={
                'rows': 3,
                'placeholder': 'Provide specific details regarding the incident, time/location observed, and any witness statements...',
                'class': 'w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-slate-50/80 focus:bg-white focus:outline-none focus:ring-1 focus:ring-brand-500 text-slate-800 placeholder-slate-400 transition'
            }),
        }

    def clean(self):
        cleaned_data = super().clean()
        student = cleaned_data.get('student')
        offense = cleaned_data.get('offense')

        if not student:
            raise ValidationError({'student': "Please select a student for this incident record."})

        if not offense or not offense.strip():
            raise ValidationError({'offense': "Please state the offense or infraction title."})

        return cleaned_data
