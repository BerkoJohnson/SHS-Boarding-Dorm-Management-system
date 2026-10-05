from django import forms
from django.utils import timezone
from django.core.exceptions import ValidationError
from .models import Exeat, Student, HouseMaster, AcademicTerm, DisciplinaryRecord


class ExeatCreateForm(forms.ModelForm):
    """
    Form for issuing new student exeat leave passes.
    
    Includes Tailwind CSS class bindings, date-time pickers, and strict
    validation for boarding status and departure/return intervals.
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
            'status',
            'approved_by',
            'approval_remarks',
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
                'class': 'w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-slate-50/80 focus:bg-white focus:outline-none focus:ring-1 focus:ring-brand-500 font-medium text-slate-800 transition'
            }),
            'approved_by': forms.Select(attrs={
                'class': 'w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-slate-50/80 focus:bg-white focus:outline-none focus:ring-1 focus:ring-brand-500 font-medium text-slate-800 transition'
            }),
            'approval_remarks': forms.Textarea(attrs={
                'rows': 2,
                'placeholder': 'Optional conditions (e.g. report back with hospital card, guardian escort required)...',
                'class': 'w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-slate-50/80 focus:bg-white focus:outline-none focus:ring-1 focus:ring-brand-500 text-slate-800 placeholder-slate-400 transition'
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Populate approved_by with active housemasters
        self.fields['approved_by'].queryset = HouseMaster.objects.select_related('house').all()
        self.fields['approved_by'].required = False
        
        # Default status to APPROVED for authorized Housemasters issuing passes
        if not self.initial.get('status'):
            self.initial['status'] = Exeat.Status.APPROVED

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
        status = cleaned_data.get('status')
        approved_by = cleaned_data.get('approved_by')

        # 1. Enforce student residency rule
        if student and student.residency == Student.ResidencyStatus.DAY:
            raise ValidationError({'student': "Day students are not boarded and do not require exeat passes."})

        # 2. Date chronological validation
        if departure and expected_return:
            if expected_return <= departure:
                raise ValidationError({
                    'expected_return': "Expected return datetime must be after scheduled departure datetime."
                })

        # 3. Approval Housemaster required if status is Approved
        if status == Exeat.Status.APPROVED and not approved_by:
            raise ValidationError({
                'approved_by': "An authorizing Housemaster must be specified when issuing an Approved pass."
            })

        return cleaned_data


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
            'logged_by',
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
            'logged_by': forms.Select(attrs={
                'class': 'w-full px-3 py-2 text-xs rounded-lg border border-slate-200 bg-slate-50/80 focus:bg-white focus:outline-none focus:ring-1 focus:ring-brand-500 font-medium text-slate-800 transition'
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['logged_by'].queryset = HouseMaster.objects.select_related('house').all()
        self.fields['logged_by'].required = False

    def clean(self):
        cleaned_data = super().clean()
        student = cleaned_data.get('student')
        offense = cleaned_data.get('offense')

        if not student:
            raise ValidationError({'student': "Please select a student for this incident record."})

        if not offense or not offense.strip():
            raise ValidationError({'offense': "Please state the offense or infraction title."})

        return cleaned_data

