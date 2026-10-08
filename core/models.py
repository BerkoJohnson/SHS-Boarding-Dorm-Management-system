import uuid
from datetime import date
from django.db import models
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.validators import RegexValidator
from django.utils import timezone
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver


class BaseModel(models.Model):
    """Abstract base model providing UUID primary keys and timestamp fields."""

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        help_text="Unique identifier generated automatically.",
    )
    created_at = models.DateTimeField(
        auto_now_add=True, help_text="Timestamp when this record was created."
    )
    updated_at = models.DateTimeField(
        auto_now=True, help_text="Timestamp when this record was last modified."
    )

    class Meta:
        abstract = True


class AcademicYear(BaseModel):
    """
    Academic Year tracking (e.g., 2025/2026 Academic Year).
    The 'name' field is automatically generated from start_date.year and end_date.year.
    """
    name = models.CharField(
        max_length=50, 
        unique=True, 
        blank=True, 
        editable=False, 
        help_text="Auto-generated from start and end years."
    )
    start_date = models.DateField()
    end_date = models.DateField()
    is_current = models.BooleanField(
        default=False, 
        help_text="Set as active academic year."
    )

    class Meta:
        ordering = ['-start_date']
        verbose_name = "Academic Year"
        verbose_name_plural = "Academic Years"

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.end_date <= self.start_date:
            raise ValidationError({'end_date': "End date must be after start date."})

    def save(self, *args, **kwargs):
        # Auto-generate name from start_date and end_date years
        if self.start_date and self.end_date:
            start_year = self.start_date.year
            end_year = self.end_date.year
            
            if start_year == end_year:
                self.name = f"{start_year} Academic Year"
            else:
                self.name = f"{start_year}/{end_year} Academic Year"

        # Ensure only one active academic year exists at a time
        if self.is_current:
            AcademicYear.objects.filter(is_current=True).exclude(pk=self.pk).update(is_current=False)

        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name or "New Academic Year"


class AcademicTerm(BaseModel):
    """Academic Term / Semester tracking supporting Double Track and Single Track schedules."""

    class TermChoice(models.TextChoices):
        # Standard Trimester Terms
        TERM_1 = "1", "Term 1"
        TERM_2 = "2", "Term 2"
        TERM_3 = "3", "Term 3"
        # GES Double Track / Transitional Semesters
        SEM_1 = "SEM1", "Semester 1"
        SEM_2 = "SEM2", "Semester 2"

    class LevelChoice(models.TextChoices):
        ALL = "ALL", "All Forms (Single Track)"
        SHS_1 = "SHS1", "SHS 1"
        SHS_2 = "SHS2", "SHS 2"
        SHS_3 = "SHS3", "SHS 3"

    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.CASCADE, related_name="terms"
    )
    term = models.CharField(max_length=10, choices=TermChoice.choices)
    level = models.CharField(
        max_length=10, 
        choices=LevelChoice.choices, 
        default=LevelChoice.ALL,
        help_text="Target form/cohort for Double Track schedules."
    )
    start_date = models.DateField()
    end_date = models.DateField()
    is_current = models.BooleanField(
        default=False, 
        help_text="Designates active term/semester for this level."
    )

    class Meta:
        ordering = ["-start_date"]
        unique_together = ("academic_year", "term", "level")
        verbose_name = "Academic Term / Semester"
        verbose_name_plural = "Academic Terms / Semesters"

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.end_date <= self.start_date:
            raise ValidationError({'end_date': "Term end date must be after start date."})

    def save(self, *args, **kwargs):
        # Reset active status ONLY for other terms matching this specific level
        if self.is_current:
            AcademicTerm.objects.filter(
                is_current=True, 
                level=self.level
            ).exclude(pk=self.pk).update(is_current=False)

        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        level_str = f" [{self.get_level_display()}]" if self.level != self.LevelChoice.ALL else ""
        return f"{self.academic_year.name} - {self.get_term_display()}{level_str}"

class Programme(BaseModel):
    """Academic programmes offered in the Senior High School."""

    name = models.CharField(
        max_length=50,
        unique=True,
        help_text="Official programme name (e.g., AGRIC, GENERAL ARTS, GENERAL SCIENCE).",
    )
    code = models.CharField(
        max_length=15,
        unique=True,
        blank=True,
        null=True,
        help_text="Optional short code for reporting (e.g., GART, GSCI).",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class House(BaseModel):
    """A school-defined boarding house."""

    name = models.CharField(
        max_length=50, help_text="House name (e.g., Red House, Blue House)."
    )
    display_order = models.PositiveSmallIntegerField(
        default=0,
        help_text="Optional ordering for house lists. Houses with the same value are sorted by name.",
    )
    motto = models.CharField(max_length=150, blank=True, null=True)

    class Meta:
        ordering = ["display_order", "name"]

    def __str__(self):
        return self.name


class Dormitory(BaseModel):
    """Physical Boarding Block/Dormitory structures strictly linked to a House."""

    class GenderBlock(models.TextChoices):
        MALE = "M", "Boys Dormitory"
        FEMALE = "F", "Girls Dormitory"

    house = models.ForeignKey(
        House, on_delete=models.CASCADE, related_name="dormitories"
    )
    name = models.CharField(
        max_length=100, help_text="Block name (e.g., House 1 Boys Block)."
    )
    gender = models.CharField(max_length=1, choices=GenderBlock.choices)
    capacity = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ["house", "gender", "name"]
        unique_together = ("house", "gender", "name")

    def __str__(self):
        return f"{self.house.name} - {self.name} ({self.get_gender_display()})"


class StaffRole(models.TextChoices):
    """Institutional system user roles for Eden High School."""
    ADMIN = "ADMIN", "System Administrator"
    SENIOR_HOUSEMASTER = "SENIOR_HOUSEMASTER", "Senior Housemaster"
    HOUSEMASTER = "HOUSEMASTER", "Housemaster / Housemistress"
    HOUSE_TUTOR = "HOUSE_TUTOR", "House Tutor"
    GATE_OFFICER = "GATE_OFFICER", "Security / Gate Officer"


class UserProfile(BaseModel):
    """
    Profile extension linking auth.User to their institutional role
    and operational boundaries in Eden High School.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
        help_text="Underlying authentication user account."
    )
    role = models.CharField(
        max_length=30,
        choices=StaffRole.choices,
        default=StaffRole.HOUSEMASTER,
        help_text="Institutional operational role governing permissions."
    )
    phone_number = models.CharField(max_length=17, blank=True)

    class Meta:
        ordering = ["user__username"]
        verbose_name = "Staff Profile"
        verbose_name_plural = "Staff Profiles"

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} ({self.get_role_display()})"

    @property
    def is_admin(self) -> bool:
        return self.user.is_superuser or self.role == StaffRole.ADMIN

    @property
    def is_senior_housemaster(self) -> bool:
        return self.role == StaffRole.SENIOR_HOUSEMASTER or (
            hasattr(self.user, 'housemaster') and self.user.housemaster and self.user.housemaster.is_senior_housemaster
        )

    @property
    def is_housemaster(self) -> bool:
        return self.role in (StaffRole.HOUSEMASTER, StaffRole.SENIOR_HOUSEMASTER) or hasattr(self.user, 'housemaster')

    @property
    def is_gate_officer(self) -> bool:
        return self.role == StaffRole.GATE_OFFICER or self.is_admin

    def can_approve_exeat(self, exeat) -> bool:
        """
        Determines whether this user has authorization to approve or reject
        a student's exeat leave request.
        """
        if self.is_admin or self.is_senior_housemaster:
            return True
        if hasattr(self.user, 'housemaster') and self.user.housemaster:
            hm = self.user.housemaster
            if hm.is_senior_housemaster:
                return True
            if hm.house and exeat.student and exeat.student.house_id == hm.house_id:
                return True
        # Backwards-compatibility fallback: lookup by staff_id or username
        hm = HouseMaster.objects.filter(
            models.Q(staff_id__iexact=self.user.username) | models.Q(full_name__icontains=self.user.username)
        ).first()
        if hm:
            if hm.is_senior_housemaster:
                return True
            if hm.house and exeat.student and exeat.student.house_id == hm.house_id:
                return True
        return False

    def can_manage_conduct(self, student=None) -> bool:
        """
        Determines if user can record disciplinary infractions.
        Admins, Senior Housemasters, Housemasters, and Tutors can all record incidents.
        """
        if self.is_admin or self.is_senior_housemaster or self.role == StaffRole.HOUSE_TUTOR:
            return True
        if hasattr(self.user, 'housemaster'):
            return True
        return False

    def can_log_gate_movement(self) -> bool:
        """
        Only system administrators and assigned Gate Officers can log gate movements.
        """
        return self.is_admin or self.role == StaffRole.GATE_OFFICER


class HouseMaster(BaseModel):
    """Housemasters / Housemistresses supervising houses or dormitory blocks."""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="housemaster",
        help_text="Login user account associated with this housemaster profile."
    )
    full_name = models.CharField(max_length=150)
    staff_id = models.CharField(max_length=30, unique=True)
    phone_number = models.CharField(max_length=17)
    
    house = models.ForeignKey(
        House, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='housemasters',
        help_text="House assigned to this master. Senior Housemaster can be left blank for school-wide oversight."
    )
    assigned_dormitory = models.ForeignKey(
        Dormitory, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        help_text="Optional specific dormitory block assigned to this housemaster."
    )
    is_senior_housemaster = models.BooleanField(
        default=False,
        help_text="Designates the Senior Housemaster. Only one staff member can hold this title."
    )

    class Meta:
        ordering = ['full_name']
        constraints = [
            models.UniqueConstraint(
                fields=['is_senior_housemaster'],
                condition=models.Q(is_senior_housemaster=True),
                name='unique_senior_housemaster'
            )
        ]

    def clean(self):
        super().clean()
        # 1. Enforce that regular Housemasters MUST be assigned to a House
        if not self.is_senior_housemaster and self.house is None:
            raise ValidationError({
                'house': "Regular Housemasters must be assigned to a specific House."
            })

        # 2. Guarantee only one Senior Housemaster exists across the institution
        if self.is_senior_housemaster:
            existing = HouseMaster.objects.filter(is_senior_housemaster=True).exclude(pk=self.pk)
            if existing.exists():
                raise ValidationError({
                    'is_senior_housemaster': f"A Senior Housemaster already exists ({existing.first().full_name}). "
                                             f"Unmark the active Senior Housemaster before assigning a new one."
                })

        # 3. Ensure assigned dormitory belongs to the selected House (if both are selected)
        if self.assigned_dormitory and self.house and self.assigned_dormitory.house_id != self.house_id:
            raise ValidationError({
                'assigned_dormitory': f"Dormitory '{self.assigned_dormitory.name}' does not belong to {self.house.name}."
            })

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
        if self.user:
            profile, _ = UserProfile.objects.get_or_create(user=self.user)
            target_role = StaffRole.SENIOR_HOUSEMASTER if self.is_senior_housemaster else StaffRole.HOUSEMASTER
            if profile.role not in (StaffRole.GATE_OFFICER, StaffRole.HOUSE_TUTOR) and profile.role != target_role:
                profile.role = target_role
                profile.save(update_fields=['role'])

    def __str__(self):
        if self.is_senior_housemaster:
            house_str = f" ({self.house.name})" if self.house else " (School-Wide)"
            return f"{self.full_name} - Senior Housemaster{house_str}"
        return f"{self.full_name} ({self.house.name if self.house else 'Unassigned'})"


class ClassGroup(BaseModel):
    """Student Form and Class allocation (e.g., 1AS1, 1B1, 1A3)."""

    name = models.CharField(max_length=20, unique=True)
    programme = models.ForeignKey(
        Programme,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="classes",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Guardian(BaseModel):
    """Parent or Guardian contact details."""

    phone_regex = RegexValidator(
        regex=r"^\+?[0-9]{9,15}$",
        message="Enter a valid phone number (digits only or international format e.g. +233240000000).",
    )

    full_name = models.CharField(max_length=150)
    phone_number = models.CharField(
        validators=[phone_regex], max_length=17, db_index=True
    )
    alt_phone_number = models.CharField(
        validators=[phone_regex], max_length=17, blank=True, null=True
    )

    class Meta:
        ordering = ["full_name"]

    def __str__(self):
        return f"{self.full_name} ({self.phone_number})"


class Student(BaseModel):
    """Primary Student Record."""

    class Gender(models.TextChoices):
        MALE = "male", "Male"
        FEMALE = "female", "Female"

    class ResidencyStatus(models.TextChoices):
        BOARDING = "Boarding", "Boarding"
        DAY = "Day", "Day"

    class EnrollmentStatus(models.TextChoices):
        ENROLLED = "Enrolled", "Enrolled"
        APPROVED = "Approved", "Approved"
        IN_PROGRESS = "Forms In Progress", "Forms In Progress"

    admission_no = models.CharField(max_length=30, unique=True, db_index=True)
    index_number = models.CharField(max_length=20, unique=True, db_index=True)
    full_name = models.CharField(max_length=200)
    gender = models.CharField(max_length=10, choices=Gender.choices)
    date_of_birth = models.DateField(null=True, blank=True)

    programme = models.ForeignKey(
        Programme, on_delete=models.PROTECT, related_name="students"
    )
    house = models.ForeignKey(
        House, on_delete=models.SET_NULL, null=True, blank=True, related_name="students"
    )
    dormitory = models.ForeignKey(
        Dormitory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="boarders",
    )
    class_group = models.ForeignKey(
        ClassGroup,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="students",
    )
    guardian = models.ForeignKey(
        Guardian,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dependents",
    )

    residency = models.CharField(
        max_length=10, choices=ResidencyStatus.choices, default=ResidencyStatus.BOARDING
    )
    status = models.CharField(
        max_length=20,
        choices=EnrollmentStatus.choices,
        default=EnrollmentStatus.ENROLLED,
    )

    class Meta:
        ordering = ["full_name"]

    def clean(self):
        super().clean()
        if self.residency == self.ResidencyStatus.DAY and self.dormitory is not None:
            raise ValidationError(
                {"dormitory": "Day students cannot be assigned to a dormitory block."}
            )
        if self.residency == self.ResidencyStatus.BOARDING and self.house is None:
            raise ValidationError(
                {"house": "Boarding students must be assigned to a House."}
            )
        if self.residency == self.ResidencyStatus.BOARDING and self.dormitory is None:
            raise ValidationError(
                {
                    "dormitory": "Boarding students must be assigned to a dormitory block."
                }
            )
        if self.dormitory and self.house and self.dormitory.house_id != self.house_id:
            raise ValidationError(
                {
                    "dormitory": f"House & Dormitory Mismatch! Dormitory '{self.dormitory.name}' belongs to "
                    f"'{self.dormitory.house.name}', but student is assigned to '{self.house.name}'."
                }
            )
        if self.dormitory:
            gender_map = {
                "male": Dormitory.GenderBlock.MALE,
                "female": Dormitory.GenderBlock.FEMALE,
            }
            if self.dormitory.gender != gender_map.get(self.gender):
                raise ValidationError(
                    {
                        "dormitory": f"Gender Mismatch! Cannot place a {self.get_gender_display()} student in a "
                        f"{self.dormitory.get_gender_display()}."
                    }
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
        
        
    @property
    def level(self):
        """Extracts level string (SHS1, SHS2, SHS3) from assigned class group name."""
        if self.class_group and self.class_group.name:
            name = self.class_group.name.strip().upper()
            if name.startswith('1'):
                return AcademicTerm.LevelChoice.SHS_1
            elif name.startswith('2'):
                return AcademicTerm.LevelChoice.SHS_2
            elif name.startswith('3'):
                return AcademicTerm.LevelChoice.SHS_3
        return AcademicTerm.LevelChoice.ALL

    def __str__(self):
        return f"{self.full_name} ({self.admission_no})"


class Exeat(BaseModel):
    """Core Exeat request and approval lifecycle (Housemaster & Student Workflow)."""
    class ExeatType(models.TextChoices):
        MEDICAL = 'Medical', 'Medical Exeat'
        TOWN = 'Town', 'Town Day Exeat'
        WEEKEND = 'Weekend', 'Weekend Exeat'
        EMERGENCY = 'Emergency', 'Emergency / Personal Exeat'
        VACATION = 'Vacation', 'End of Term / Vacation Exeat'

    class Status(models.TextChoices):
        PENDING = 'Pending', 'Pending Approval'
        APPROVED = 'Approved', 'Approved (Pass Issued)'
        REJECTED = 'Rejected', 'Rejected'
        CANCELLED = 'Cancelled', 'Cancelled'

    exeat_number = models.CharField(
        max_length=30, 
        unique=True, 
        editable=False, 
        help_text="Auto-generated exeat reference number e.g. EXT-20261004-A1B2"
    )
    student = models.ForeignKey(
        Student, 
        on_delete=models.CASCADE, 
        related_name='exeats',
        help_text="Student requesting leave."
    )
    academic_term = models.ForeignKey(
        AcademicTerm, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='exeats'
    )
    exeat_type = models.CharField(max_length=20, choices=ExeatType.choices, default=ExeatType.MEDICAL)
    reason = models.TextField(help_text="Detailed reason for leave request.")
    destination = models.CharField(max_length=200, help_text="Destination address or town during leave.")
    
    # Scheduled Travel Timestamps
    expected_departure = models.DateTimeField(help_text="Scheduled departure date & time.")
    expected_return = models.DateTimeField(help_text="Scheduled return date & time.")

    # Status & Approvals
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    approved_by = models.ForeignKey(
        HouseMaster, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='approved_exeats',
        help_text="Housemaster / Senior Housemaster who signed off this pass (Approved only)."
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='drafted_exeats',
        help_text="Staff member who originally drafted / submitted this leave request (audit trail)."
    )
    approval_remarks = models.TextField(blank=True, null=True, help_text="Housemaster's notes or conditions.")

    class Meta:
        ordering = ['-expected_departure']
        verbose_name = "Exeat / Leave Pass"
        verbose_name_plural = "Exeats / Leave Passes"
        indexes = [
            models.Index(fields=['exeat_number']),
            models.Index(fields=['status', 'expected_return']),
        ]

    def clean(self):
        super().clean()
        # 1. Day students validation
        if self.student_id and getattr(self, 'student', None) and self.student.residency == Student.ResidencyStatus.DAY:
            raise ValidationError({'student': "Day students do not require boarding exeat passes."})

        # 2. Date order validation
        if self.expected_departure and self.expected_return:
            if self.expected_return <= self.expected_departure:
                raise ValidationError({'expected_return': "Expected return time must be after expected departure time."})

        # 3. Require approval details when status is Approved
        if self.status == self.Status.APPROVED and not self.approved_by_id:
            raise ValidationError({'approved_by': "An approving Housemaster must be specified when issuing an exeat pass."})

        # 4. Jurisdiction guard: the signatory Housemaster must actually have authority over this student's house
        if self.status == self.Status.APPROVED and self.approved_by_id and self.student_id:
            hm = self.approved_by
            if not hm.is_senior_housemaster:
                student_house_id = self.student.house_id if getattr(self.student, 'house_id', None) else None
                if hm.house_id is None:
                    # Non-senior Housemaster with no assigned house cannot approve anything
                    raise ValidationError({
                        'approved_by': f"{hm.full_name} is not assigned to a House and is not the Senior Housemaster. "
                                        f"Only the student's Housemaster or the Senior Housemaster can approve this pass."
                    })
                if student_house_id is not None and hm.house_id != student_house_id:
                    raise ValidationError({
                        'approved_by': f"Jurisdiction error: {hm.full_name} is the Housemaster for {hm.house.name}, "
                                        f"but this student belongs to {self.student.house.name if self.student.house else 'an unassigned House'}. "
                                        f"Approval is restricted to the student's own Housemaster or the Senior Housemaster."
                    })

    def save(self, *args, **kwargs):
        # 1. Auto-generate unique Exeat Pass Number on creation
        if not self.exeat_number:
            timestamp_str = timezone.now().strftime("%Y%m%d")
            random_uuid = str(self.id)[:4].upper()
            self.exeat_number = f"EXT-{timestamp_str}-{random_uuid}"

        # 2. Auto-assign active term matching the student's form level
        if not self.academic_term_id and self.student_id:
            student_level = self.student.level
            current_term = AcademicTerm.objects.filter(
                is_current=True,
                level__in=[student_level, AcademicTerm.LevelChoice.ALL]
            ).order_by('-created_at').first()

            if current_term:
                self.academic_term = current_term

        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def is_approved(self):
        return self.status == self.Status.APPROVED

    @property
    def current_state(self):
        """Dynamic helper combining request status and gate movement state safely."""
        if self.status != self.Status.APPROVED:
            return self.get_status_display()
        try:
            return self.reconciliation.state_display
        except ObjectDoesNotExist:
            pass
        try:
            return self.movement.state_display
        except ObjectDoesNotExist:
            return self._scheduled_movement_state()

    def _scheduled_movement_state(self, now=None):
        now = now or timezone.now()
        if now >= self.expected_return:
            return "Pass Unresolved"
        if now >= self.expected_departure:
            return "Departure Not Recorded"
        return "Awaiting Departure"

    def __str__(self):
        return f"{self.exeat_number} - {self.student.full_name} ({self.get_exeat_type_display()})"


class ExeatMovement(BaseModel):
    """Gate control log tracking real-time departures and arrivals (Security/Gate Workflow)."""
    exeat = models.OneToOneField(
        Exeat, 
        on_delete=models.CASCADE, 
        related_name='movement',
        help_text="Associated approved exeat pass."
    )
    actual_departure = models.DateTimeField(null=True, blank=True, help_text="Timestamp when student exited the school gate.")
    actual_return = models.DateTimeField(null=True, blank=True, help_text="Timestamp when student returned through the gate.")
    departure_recorded_by = models.CharField(max_length=100, blank=True, null=True, help_text="Gate officer or master name/ID.")
    return_recorded_by = models.CharField(max_length=100, blank=True, null=True, help_text="Gate officer or master name/ID.")

    class Meta:
        verbose_name = "Exeat Gate Log"
        verbose_name_plural = "Exeat Gate Logs"

    def clean(self):
        super().clean()
        # 1. Gate movement can only be created for APPROVED exeats
        if self.exeat_id and self.exeat.status != Exeat.Status.APPROVED:
            raise ValidationError({'exeat': f"Cannot record gate movements for exeat pass '{self.exeat.exeat_number}' because it is {self.exeat.get_status_display()}."})

        # 2. Return timestamp must be after departure timestamp
        if self.actual_departure and self.actual_return:
            if self.actual_return <= self.actual_departure:
                raise ValidationError({'actual_return': "Actual return time must be after actual departure time."})

        # 3. Cannot log return before departure is logged
        if self.actual_return and not self.actual_departure:
            raise ValidationError({'actual_return': "Cannot log return timestamp before actual departure has been logged."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def is_currently_out(self):
        """Returns True if student has departed but not yet returned."""
        return self.actual_departure is not None and self.actual_return is None

    @property
    def is_overdue(self):
        """Returns True if student is currently out past their expected return datetime."""
        if self.is_currently_out and self.exeat.expected_return:
            return timezone.now() >= self.exeat.expected_return
        return False

    @property
    def state_display(self):
        """Returns readable state badge string for frontend rendering."""
        if self.actual_return:
            return "Returned"
        if self.is_overdue:
            return "OVERDUE (Student Out)"
        if self.is_currently_out:
            return "Active (Student Out)"
        if self.exeat.status == Exeat.Status.APPROVED:
            return self.exeat._scheduled_movement_state()
        return "Pending Departure"

    def __str__(self):
        return f"Gate Log: {self.exeat.exeat_number} - {self.state_display}"


class ExeatReconciliation(BaseModel):
    """Audited staff resolution for an approved pass with missing gate records."""

    class Outcome(models.TextChoices):
        UNUSED = "unused", "Pass not used"
        STUDENT_OUT = "student_out", "Student travelled and is still out"
        RETURNED = "returned", "Student travelled and has returned"

    exeat = models.OneToOneField(
        Exeat,
        on_delete=models.CASCADE,
        related_name="reconciliation",
    )
    outcome = models.CharField(max_length=20, choices=Outcome.choices)
    actual_departure = models.DateTimeField(null=True, blank=True)
    actual_return = models.DateTimeField(null=True, blank=True)
    reason = models.TextField(help_text="Evidence or explanation for reconciling missing gate records.")
    reconciled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reconciled_exeats",
    )
    return_recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reconciled_exeat_returns",
    )

    class Meta:
        verbose_name = "Exeat Movement Reconciliation"
        verbose_name_plural = "Exeat Movement Reconciliations"

    def clean(self):
        super().clean()
        if self.exeat_id and self.exeat.status != Exeat.Status.APPROVED:
            raise ValidationError({"exeat": "Only approved exeats can be reconciled."})

        movement = ExeatMovement.objects.filter(exeat_id=self.exeat_id).first() if self.exeat_id else None
        if movement and (movement.actual_departure or movement.actual_return):
            raise ValidationError({"exeat": "A pass with recorded gate movement cannot be reconciled."})

        if self.outcome == self.Outcome.UNUSED:
            if self.actual_departure or self.actual_return:
                raise ValidationError({"outcome": "An unused pass cannot have travel timestamps."})
        elif self.outcome == self.Outcome.STUDENT_OUT:
            if not self.actual_departure:
                raise ValidationError({"actual_departure": "Record the confirmed or best-known departure time."})
            if self.actual_return:
                raise ValidationError({"actual_return": "A student still out cannot have a return time."})
        elif self.outcome == self.Outcome.RETURNED:
            if not self.actual_departure:
                raise ValidationError({"actual_departure": "Record the confirmed or best-known departure time."})
            if not self.actual_return:
                raise ValidationError({"actual_return": "Record the confirmed or best-known return time."})

        if self.actual_departure and self.actual_return and self.actual_return <= self.actual_departure:
            raise ValidationError({"actual_return": "Actual return must be after actual departure."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def state_display(self):
        if self.outcome == self.Outcome.UNUSED:
            return "Pass Unused"
        if self.outcome == self.Outcome.RETURNED:
            return "Returned (Reconciled)"
        if timezone.now() >= self.exeat.expected_return:
            return "OVERDUE (Reconciled)"
        return "Student Out (Reconciled)"

    def __str__(self):
        return f"Reconciliation: {self.exeat.exeat_number} ({self.get_outcome_display()})"


class SMSDelivery(BaseModel):
    """Durable per-recipient record for an outbound SMS notification."""

    class EventType(models.TextChoices):
        REQUESTED = "requested", "Exeat request submitted"
        APPROVED = "approved", "Exeat approved"
        REJECTED = "rejected", "Exeat rejected"
        DEPARTURE = "departure", "Student departed campus"
        RETURN = "return", "Student returned to campus"
        OVERDUE = "overdue", "Exeat overdue"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        PROCESSING = "processing", "Processing"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    exeat = models.ForeignKey(
        Exeat,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="sms_deliveries",
    )
    event_type = models.CharField(max_length=30, choices=EventType.choices)
    recipient_name = models.CharField(max_length=150, blank=True)
    phone_number = models.CharField(max_length=17, db_index=True)
    message = models.TextField()
    status = models.CharField(
        max_length=15,
        choices=Status.choices,
        default=Status.QUEUED,
    )
    provider_reference = models.CharField(max_length=200, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_attempted_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    dedupe_key = models.CharField(max_length=200, unique=True, db_index=True)
    scheduled_for = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "SMS Delivery"
        verbose_name_plural = "SMS Deliveries"

    def __str__(self):
        return (
            f"{self.get_event_type_display()} to {self.phone_number} "
            f"({self.get_status_display()})"
        )


class SMSConfiguration(models.Model):
    """Singleton admin configuration for enabling or pausing SMS notifications."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    enabled = models.BooleanField(
        default=False,
        help_text="When disabled, no new SMS notifications will be sent.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "SMS Configuration"
        verbose_name_plural = "SMS Configuration"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def is_enabled(cls) -> bool:
        configuration = cls.objects.filter(pk=1).only("enabled").first()
        if configuration is None:
            return getattr(settings, "SMS_ENABLED", False)
        return configuration.enabled

    def __str__(self):
        return "SMS Notifications"


class SchoolConfiguration(models.Model):
    """Singleton public-facing identity and contact details for the school."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    name = models.CharField(max_length=150, default="EDEN HIGH SCHOOL")
    logo = models.ImageField(upload_to="school/logos/", blank=True)
    address = models.TextField(blank=True)
    contact_number = models.CharField(max_length=30, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "School Configuration"
        verbose_name_plural = "School Configuration"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        return cls.objects.filter(pk=1).first() or cls()

    def __str__(self):
        return self.name


class DisciplinaryRecord(BaseModel):
    """Conduct and disciplinary log for students."""

    class Severity(models.TextChoices):
        MINOR = "Minor", "Minor Offense"
        MAJOR = "Major", "Major Offense"
        CRITICAL = "Critical", "Critical Infraction"

    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="disciplinary_records"
    )
    academic_term = models.ForeignKey(
        AcademicTerm, on_delete=models.SET_NULL, null=True, blank=True
    )
    offense = models.CharField(
        max_length=200, help_text="e.g. Overstayed exeat, lateness, missing roll call."
    )
    description = models.TextField(blank=True, null=True)
    severity = models.CharField(
        max_length=15, choices=Severity.choices, default=Severity.MINOR
    )
    action_taken = models.CharField(
        max_length=200, help_text="e.g. Internal suspension, gating, warning letter."
    )
    logged_by = models.ForeignKey(
        HouseMaster, on_delete=models.SET_NULL, null=True, blank=True
    )
    roll_call_entry = models.ForeignKey(
        "RollCallEntry",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="disciplinary_records",
        help_text="Roll-call entry that generated this conduct record, if applicable.",
    )
    roll_call_follow_up = models.OneToOneField(
        "RollCallAbsenceFollowUp",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="disciplinary_record",
        help_text="Confirmed absence follow-up that generated this conduct record.",
    )
    corrected_at = models.DateTimeField(null=True, blank=True)
    corrected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="corrected_disciplinary_records",
    )
    correction_note = models.TextField(blank=True)

    @property
    def is_corrected(self):
        return self.corrected_at is not None

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Disciplinary Record"
        verbose_name_plural = "Disciplinary Records"

    def __str__(self):
        return f"{self.student.full_name} - {self.offense} ({self.severity})"


class RollCallSession(BaseModel):
    """
    Boarding House roll call inspection session conducted by Housemasters / Tutors.
    Tracks muster attendance per house or dormitory block for a specific date and time slot.
    """
    class SessionType(models.TextChoices):
        EVENING = "Evening", "Evening Roll Call (Lights Out)"
        MORNING = "Morning", "Morning Roll Call & Inspection"
        SPECIAL = "Special", "Emergency / Special Muster"

    class Status(models.TextChoices):
        IN_PROGRESS = "In Progress", "In Progress"
        COMPLETED = "Completed", "Completed"

    house = models.ForeignKey(
        House,
        on_delete=models.CASCADE,
        related_name="roll_call_sessions",
        help_text="House being inspected."
    )
    dormitory = models.ForeignKey(
        Dormitory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="roll_call_sessions",
        help_text="Optional specific dormitory block (or all blocks in house if blank)."
    )
    academic_term = models.ForeignKey(
        AcademicTerm,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="roll_call_sessions"
    )
    session_type = models.CharField(
        max_length=20,
        choices=SessionType.choices,
        default=SessionType.EVENING
    )
    date = models.DateField(default=date.today)
    conducted_by = models.ForeignKey(
        HouseMaster,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conducted_roll_calls",
        help_text="Staff member conducting this roll call muster."
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.IN_PROGRESS
    )
    notes = models.TextField(blank=True, null=True, help_text="General inspection notes or observations.")

    class Meta:
        ordering = ["-date", "-created_at"]
        verbose_name = "Roll Call Session"
        verbose_name_plural = "Roll Call Sessions"

    def __str__(self):
        block = f" - {self.dormitory.name}" if self.dormitory else ""
        return f"{self.house.name}{block} ({self.get_session_type_display()} - {self.date})"

    @property
    def total_students(self):
        return self.entries.count()

    @property
    def present_count(self):
        return self.entries.filter(status=RollCallEntry.Status.PRESENT).count()

    @property
    def unmarked_count(self):
        return self.entries.filter(status=RollCallEntry.Status.UNMARKED).count()

    @property
    def accounted_count(self):
        return self.total_students - self.unmarked_count

    @property
    def absent_count(self):
        return self.entries.filter(status=RollCallEntry.Status.ABSENT).count()

    @property
    def on_exeat_count(self):
        return self.entries.filter(status=RollCallEntry.Status.ON_EXEAT).count()

    @property
    def infirmary_count(self):
        return self.entries.filter(status=RollCallEntry.Status.INFIRMARY).count()

    @property
    def attendance_rate(self):
        total = self.total_students
        if not total:
            return 0
        present = self.present_count
        return round((present / total) * 100, 1)


class RollCallEntry(BaseModel):
    """
    Individual student attendance record within a RollCallSession.
    """
    class Status(models.TextChoices):
        UNMARKED = "Unmarked", "Not yet verified"
        PRESENT = "Present", "Present"
        ABSENT = "Absent", "Absent - review required"
        ON_EXEAT = "On Exeat", "On Exeat (Excused Leave)"
        INFIRMARY = "Infirmary", "Sick Bay / Infirmary"
        OFFICIAL_ACTIVITY = "Activity", "Official Activity"

    class StatusSource(models.TextChoices):
        STAFF = "staff", "Verified by staff"
        GATE = "gate", "Reconciled from gate log"

    session = models.ForeignKey(
        RollCallSession,
        on_delete=models.CASCADE,
        related_name="entries"
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="roll_call_entries"
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.UNMARKED
    )
    status_source = models.CharField(
        max_length=10,
        choices=StatusSource.choices,
        default=StatusSource.STAFF,
    )
    remarks = models.CharField(max_length=200, blank=True, null=True)
    gate_conflict_at = models.DateTimeField(null=True, blank=True)
    gate_conflict_note = models.TextField(blank=True)
    gate_conflict_reviewed_at = models.DateTimeField(null=True, blank=True)
    gate_conflict_reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_roll_call_gate_conflicts",
    )

    class Meta:
        ordering = ["student__full_name"]
        unique_together = ("session", "student")
        verbose_name = "Roll Call Entry"
        verbose_name_plural = "Roll Call Entries"

    def __str__(self):
        return f"{self.student.full_name}: {self.get_status_display()}"

    def clean(self):
        """Prevents modification of entries belonging to a finalized session."""
        super().clean()
        # Allow initial creation (no pk yet) but block updates on completed sessions
        if self.pk and self.session.status == RollCallSession.Status.COMPLETED:
            from django.core.exceptions import ValidationError
            raise ValidationError(
                "Cannot modify attendance records in a finalized roll call session. "
                "Records are locked for audit integrity."
            )

    def save(self, *args, **kwargs):
        """Enforces clean() validation before every save to prevent silent bypasses."""
        # Skip validation during bulk_create (initial roster population) — entries won't have a pk yet
        if self.pk:
            self.clean()
        super().save(*args, **kwargs)


class RollCallAbsenceFollowUp(BaseModel):
    """Review trail for an absence before it is treated as a conduct incident."""

    class Status(models.TextChoices):
        PENDING = "Pending", "Pending review"
        CONFIRMED = "Confirmed", "Confirmed unexcused"
        EXCUSED = "Excused", "Authorized absence"
        CORRECTED = "Corrected", "Roll-call entry corrected"

    entry = models.ForeignKey(
        RollCallEntry,
        on_delete=models.CASCADE,
        related_name="absence_follow_ups",
    )
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.PENDING,
    )
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reported_roll_call_absences",
    )
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_roll_call_absences",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.TextField(blank=True)
    resulting_status = models.CharField(
        max_length=20,
        choices=RollCallEntry.Status.choices,
        blank=True,
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["entry"],
                condition=models.Q(status="Pending"),
                name="one_pending_absence_follow_up_per_entry",
            )
        ]
        verbose_name = "Roll Call Absence Follow-Up"
        verbose_name_plural = "Roll Call Absence Follow-Ups"

    def __str__(self):
        return f"{self.entry.student.full_name} - {self.get_status_display()}"


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def ensure_user_profile(sender, instance, created, **kwargs):
    """Automatically provisions a UserProfile whenever a new User is registered."""
    if created:
        role = StaffRole.ADMIN if instance.is_superuser else StaffRole.HOUSEMASTER
        UserProfile.objects.get_or_create(user=instance, defaults={'role': role})


def get_user_profile(user):
    """Safely retrieves or provisions a UserProfile for any authenticated user."""
    if not user or not user.is_authenticated:
        return None
    try:
        profile = UserProfile.objects.get(user=user)
    except UserProfile.DoesNotExist:
        role = StaffRole.ADMIN if user.is_superuser else StaffRole.HOUSEMASTER
        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'role': role})

    # Keep role in sync if linked to a HouseMaster
    hm = getattr(user, 'housemaster', None)
    if hm:
        target_role = StaffRole.SENIOR_HOUSEMASTER if hm.is_senior_housemaster else StaffRole.HOUSEMASTER
        if profile.role not in (StaffRole.GATE_OFFICER, StaffRole.HOUSE_TUTOR) and profile.role != target_role:
            profile.role = target_role
            profile.save(update_fields=['role'])

    return profile


def get_approvable_pending_for_user(user):
    """
    Returns a QuerySet of PENDING exeat requests that the given user is
    jurisdictionally authorized to approve or reject.

    Single source of truth used by: context-processor notification badges,
    the Pending Approvals hub, the Exeats list 'My Approvals' filter, and
    any view that needs to scope the approval queue to the current user.

    Approval rules (mirrors can_approve_exeat jurisdiction):
      * Superusers / System Administrators -> ALL pending exeats
      * Senior Housemaster -> ALL pending exeats (school-wide oversight)
      * Regular Housemaster -> pending exeats for students in their assigned House only
      * Tutors / Gate Officers / any other role -> empty QuerySet

    Args:
        user: A Django auth User instance (typically request.user).

    Returns:
        QuerySet[Exeat]: scoped, select_related for list rendering.
    """
    base_qs = Exeat.objects.filter(
        status=Exeat.Status.PENDING
    ).select_related(
        'student', 'student__house', 'student__dormitory',
        'academic_term', 'created_by'
    ).order_by('-created_at')

    if not user or not user.is_authenticated:
        return base_qs.none()

    # Superuser shortcut: full institutional authority
    if user.is_superuser:
        return base_qs

    profile = get_user_profile(user)

    # Role guard — only Admin / Senior HM / Housemaster can approve anything
    if not profile:
        return base_qs.none()
    if profile.role not in (StaffRole.ADMIN, StaffRole.SENIOR_HOUSEMASTER, StaffRole.HOUSEMASTER):
        return base_qs.none()

    # ADMIN and SENIOR_HOUSEMASTER: school-wide
    if profile.role in (StaffRole.ADMIN, StaffRole.SENIOR_HOUSEMASTER):
        return base_qs

    # HOUSEMASTER: only their directly assigned house
    hm = getattr(user, 'housemaster', None)

    if hm:
        # Senior flag could be set on the HouseMaster row even if role on profile drifted
        if hm.is_senior_housemaster:
            return base_qs
        if hm.house_id is not None:
            return base_qs.filter(student__house_id=hm.house_id)

    return base_qs.none()


def get_exeat_queue_stats(scoped_pending_qs, user):
    """
    Quick aggregations for the dedicated Pending Approvals Hub header stat cards
    and per-house queue chips.

    Returns a dict with keys:
      total_pending, oldest_pending_hours, avg_queue_age_hours,
      approved_today, rejected_today, overdue_returns, per_house (list of dicts).

    Args:
        scoped_pending_qs: an already-jurisdiction-scoped PENDING Exeat queryset
            (e.g. the output of get_approvable_pending_for_user(request.user)).
        user: the currently-logged-in Django User instance (used to attribute
            'Approved Today' to the current signatory, for SLA-awareness).
    """
    from django.db.models import Avg, Min, Count, Q as _Q, F
    from django.utils import timezone

    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    stats = {
        'total_pending': scoped_pending_qs.count(),
        'oldest_pending_hours': 0,
        'avg_queue_age_hours': 0,
        'approved_today': 0,
        'rejected_today': 0,
        'overdue_returns': 0,
        'per_house': [],
    }

    # --- Oldest + average age (in hours) for the SLA gauges ---
    agg = scoped_pending_qs.aggregate(
        oldest_created=Min('created_at'),
        avg_created=Avg('created_at'),
    )
    if agg['oldest_created']:
        age_delta = now - agg['oldest_created']
        stats['oldest_pending_hours'] = round(age_delta.total_seconds() / 3600, 1)
    if agg['avg_created']:
        avg_delta = now - agg['avg_created']
        stats['avg_queue_age_hours'] = round(avg_delta.total_seconds() / 3600, 1)

    # --- Today's throughput for the current approver (SLA context) ---
    # Approved_today attributed to the currently-logged-in approver so each
    # HM sees *their own* desk stats, not a system-wide pool.
    today_by_me = Exeat.objects.filter(
        updated_at__gte=today_start,
        approved_by__in=list(
            HouseMaster.objects.filter(
                _Q(staff_id__iexact=user.username) | _Q(full_name__icontains=user.username)
            ).values_list('id', flat=True)
        ) if user.is_authenticated else _Q(pk__in=[]),
    ) if user.is_authenticated else Exeat.objects.none()
    if getattr(user, 'housemaster', None):
        today_by_me = Exeat.objects.filter(
            updated_at__gte=today_start,
            approved_by_id=user.housemaster.id,
        )
    stats['approved_today'] = today_by_me.filter(status=Exeat.Status.APPROVED).count()
    stats['rejected_today'] = today_by_me.filter(status=Exeat.Status.REJECTED).count()

    # --- Overdue currently-out (cross queue context for approvers) ---
    stats['overdue_returns'] = Exeat.objects.filter(
        status=Exeat.Status.APPROVED,
        expected_return__lte=now,
    ).filter(
        _Q(movement__actual_departure__isnull=False, movement__actual_return__isnull=True)
        | _Q(
            reconciliation__outcome=ExeatReconciliation.Outcome.STUDENT_OUT,
            reconciliation__actual_return__isnull=True,
        )
    ).filter(
        # For regular Housemasters scope overdue to their own house, not global
        student__house_id__in=list(
            set(
                e.student.house_id
                for e in scoped_pending_qs.select_related('student__house')
                if e.student and e.student.house_id
            )
        ) if not (user.is_superuser or (getattr(get_user_profile(user), 'role', None) in (StaffRole.ADMIN, StaffRole.SENIOR_HOUSEMASTER)))
        else _Q(pk__isnull=False)  # admin/senior: unrestricted
    ).count()

    # --- Per-house breakdown (for the queue-jump chip bar) ---
    house_totals = list(
        scoped_pending_qs
        .values(house_id=F('student__house_id'), house_name=F('student__house__name'), house_order=F('student__house__display_order'))
        .annotate(pending=Count('id'))
        .order_by('house_order', 'house_name')
    )
    stats['per_house'] = [
        {
            'id': row['house_id'],
            'name': row['house_name'] or 'Unassigned',
            'pending': row['pending'],
        }
        for row in house_totals if row['house_id'] is not None
    ]
    # Also carry an "All houses" chip with total count
    if stats['per_house']:
        stats['per_house'].insert(0, {'id': None, 'name': 'All Queues', 'pending': stats['total_pending']})

    return stats