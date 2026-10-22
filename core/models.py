import posixpath
import uuid
from datetime import date, timedelta
from django.db import models, transaction
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.validators import (
    FileExtensionValidator,
    MaxLengthValidator,
    MaxValueValidator,
    MinValueValidator,
    RegexValidator,
)
from django.utils import timezone
from django.conf import settings
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver
from PIL import Image, UnidentifiedImageError
from django_pgschemas.schema import get_current_schema
from .storage import PrivateStudentPhotoStorage

MAX_STUDENT_PHOTO_SIZE = 5 * 1024 * 1024
MAX_STUDENT_PHOTO_PIXELS = 25_000_000
ROLL_CALL_SESSION_TIMEOUT = timedelta(hours=1)


def tenant_upload_path(directory, filename):
    if settings.TENANCY_ENABLED:
        schema = get_current_schema()
        schema_name = getattr(schema, "schema_name", None) if schema else None
        if schema_name and schema_name != "public":
            directory = posixpath.join("tenants", schema_name, directory)
    return posixpath.join(directory, filename)


def student_photo_upload_to(instance, filename):
    directory = timezone.now().strftime("students/photos/%Y/%m")
    return tenant_upload_path(directory, filename)


def school_logo_upload_to(instance, filename):
    return tenant_upload_path("school/logos", filename)


def validate_student_photo(photo):
    if photo.size > MAX_STUDENT_PHOTO_SIZE:
        raise ValidationError("Student photos must be 5 MB or smaller.")
    try:
        with Image.open(photo) as image:
            image_format = image.format
            if image.width * image.height > MAX_STUDENT_PHOTO_PIXELS:
                raise ValidationError(
                    "Student photos must be 25 megapixels or smaller."
                )
            image.verify()
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
    ) as error:
        raise ValidationError("Upload a valid JPEG or PNG image.") from error
    finally:
        photo.seek(0)
    if image_format not in {"JPEG", "PNG"}:
        raise ValidationError("Student photos must be JPEG or PNG images.")


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
        help_text="Auto-generated from start and end years.",
    )
    start_date = models.DateField()
    end_date = models.DateField()
    is_current = models.BooleanField(
        default=False, help_text="Set as active academic year."
    )
    students_phased_out_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When automatic SHS 3 completion ran for this academic year.",
    )

    class Meta:
        ordering = ["-start_date"]
        verbose_name = "Academic Year"
        verbose_name_plural = "Academic Years"

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.end_date <= self.start_date:
            raise ValidationError({"end_date": "End date must be after start date."})

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
            AcademicYear.objects.filter(is_current=True).exclude(pk=self.pk).update(
                is_current=False
            )

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
        help_text="Target form/cohort for Double Track schedules.",
    )
    start_date = models.DateField()
    end_date = models.DateField()
    is_current = models.BooleanField(
        default=False, help_text="Designates active term/semester for this level."
    )

    class Meta:
        ordering = ["-start_date"]
        unique_together = ("academic_year", "term", "level")
        constraints = [
            models.UniqueConstraint(
                fields=["level"],
                condition=models.Q(is_current=True),
                name="unique_current_academic_term_per_level",
            )
        ]
        verbose_name = "Academic Term / Semester"
        verbose_name_plural = "Academic Terms / Semesters"

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.end_date <= self.start_date:
            raise ValidationError(
                {"end_date": "Term end date must be after start date."}
            )

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if self.is_current:
                from core.services.configuration import lock_academic_term_updates

                lock_academic_term_updates()
                list(
                    AcademicTerm.objects.select_for_update()
                    .filter(level=self.level)
                    .order_by("pk")
                    .values_list("pk", flat=True)
                )
                AcademicTerm.objects.filter(
                    is_current=True,
                    level=self.level,
                ).exclude(pk=self.pk).update(is_current=False)

            self.full_clean()
            super().save(*args, **kwargs)

    def __str__(self):
        level_str = (
            f" [{self.get_level_display()}]"
            if self.level != self.LevelChoice.ALL
            else ""
        )
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

    def clean(self):
        super().clean()
        if self._state.adding:
            from tenancy.entitlements import ensure_quota_available

            ensure_quota_available("max_dormitories", Dormitory.objects.count())
        elif self.pk:
            occupied = Student.objects.exclude(
                status=Student.EnrollmentStatus.COMPLETED
            ).filter(
                dormitory_id=self.pk,
                residency=Student.ResidencyStatus.BOARDING,
            ).count()
            now = timezone.now()
            exception_places = sum(
                DormitoryCapacityException.objects.filter(
                    dormitory_id=self.pk,
                    cancelled_at__isnull=True,
                    starts_at__lte=now,
                    ends_at__gte=now,
                ).values_list("additional_capacity", flat=True)
            )
            if occupied > self.capacity + exception_places:
                raise ValidationError(
                    {
                        "capacity": (
                            f"Capacity cannot be lower than the {occupied} active "
                            "boarding student(s) currently assigned to this "
                            "dormitory, less any approved capacity exception."
                        )
                    }
                )

    def __str__(self):
        return f"{self.house.name} - {self.name} ({self.get_gender_display()})"

    def save(self, *args, **kwargs):
        if self._state.adding:
            from tenancy.entitlements import reserve_quota

            # Enforce the dormitory quota and perform the insert inside the same
            # transaction-scoped advisory lock so concurrent creates cannot
            # overshoot the plan limit.
            with reserve_quota("max_dormitories", Dormitory.objects.count):
                self.full_clean()
                return super().save(*args, **kwargs)
        with transaction.atomic():
            # Serialize capacity reductions with placements.  Calling full_clean
            # here is essential: admin forms already validate, but direct model
            # saves must not be able to hide active boarders by lowering capacity.
            type(self).objects.select_for_update().filter(pk=self.pk).exists()
            self.full_clean()
            return super().save(*args, **kwargs)


class DormitoryRoom(BaseModel):
    """A room inside a dormitory block with a hard boarder capacity."""

    dormitory = models.ForeignKey(
        Dormitory, on_delete=models.CASCADE, related_name="rooms"
    )
    name = models.CharField(max_length=100)
    capacity = models.PositiveSmallIntegerField(
        help_text="Maximum number of boarding students assigned to this room."
    )

    class Meta:
        ordering = ["dormitory__house", "dormitory", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["dormitory", "name"],
                name="unique_room_name_per_dormitory",
            ),
            models.CheckConstraint(
                condition=models.Q(capacity__gt=0),
                name="dormitory_room_capacity_positive",
            ),
        ]

    def clean(self):
        super().clean()
        if self.pk:
            previous = (
                type(self).objects.filter(pk=self.pk).values("dormitory_id").first()
            )
            occupied = (
                Student.objects.exclude(status=Student.EnrollmentStatus.COMPLETED)
                .filter(
                    room_id=self.pk,
                    residency=Student.ResidencyStatus.BOARDING,
                )
                .count()
            )
            if occupied and previous and previous["dormitory_id"] != self.dormitory_id:
                raise ValidationError(
                    {
                        "dormitory": (
                            "A room with assigned boarding students cannot be "
                            "moved to another dormitory."
                        )
                    }
                )
            if occupied > self.capacity:
                raise ValidationError(
                    {
                        "capacity": (
                            f"Capacity cannot be lower than the {occupied} "
                            "boarding student(s) currently assigned to this room."
                        )
                    }
                )

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if self.pk:
                # Serialize room-capacity changes with student placements.
                type(self).objects.select_for_update().filter(pk=self.pk).exists()
            self.full_clean()
            super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.dormitory} - {self.name}"


class DormitoryCapacityException(BaseModel):
    """Time-bound senior approval to exceed a dormitory's normal capacity."""

    dormitory = models.ForeignKey(
        Dormitory,
        on_delete=models.CASCADE,
        related_name="capacity_exceptions",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_dormitory_capacity_exceptions",
    )
    reason = models.TextField(
        help_text="Why the normal capacity cannot be maintained and what mitigation is in place."
    )
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField()
    additional_capacity = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(100)],
        help_text="Number of additional boarders permitted during this exception.",
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cancelled_dormitory_capacity_exceptions",
    )

    class Meta:
        ordering = ["-starts_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(additional_capacity__gt=0),
                name="dormitory_capacity_exception_positive",
            )
        ]
        verbose_name = "Dormitory Capacity Exception"
        verbose_name_plural = "Dormitory Capacity Exceptions"

    def clean(self):
        super().clean()
        if not (self.reason or "").strip():
            raise ValidationError(
                {"reason": "Record the reason for the capacity exception."}
            )
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError(
                {"ends_at": "The exception must end after it starts."}
            )
        if self.starts_at and self.ends_at:
            if self.ends_at - self.starts_at > timedelta(days=7):
                raise ValidationError(
                    {
                        "ends_at": (
                            "A single capacity exception cannot exceed seven days. "
                            "Create a new, reviewed exception for a longer period."
                        )
                    }
                )
        if self.dormitory_id and self.starts_at and self.ends_at and not self.cancelled_at:
            overlapping = DormitoryCapacityException.objects.filter(
                dormitory_id=self.dormitory_id,
                cancelled_at__isnull=True,
                starts_at__lt=self.ends_at,
                ends_at__gt=self.starts_at,
            )
            if self.pk:
                overlapping = overlapping.exclude(pk=self.pk)
            if overlapping.exists():
                raise ValidationError(
                    {
                        "starts_at": (
                            "This dormitory already has an overlapping capacity "
                            "exception. Cancel it or wait for it to expire before "
                            "creating another."
                        )
                    }
                )
        if not self.approved_by_id and not self.cancelled_at:
            raise ValidationError(
                {"approved_by": "A senior approver is required for a capacity exception."}
            )
        if self.approved_by_id and not self.cancelled_at:
            profile = get_user_profile(self.approved_by)
            if profile is None or not (
                profile.is_admin or profile.is_senior_housemaster
            ):
                raise ValidationError(
                    {
                        "approved_by": (
                            "Only the Headmaster/ADMIN or Senior Housemaster may "
                            "approve a dormitory capacity exception."
                        )
                    }
                )

    def save(self, *args, **kwargs):
        # Serialize capacity-exception changes with student placements and other
        # exception changes for the same dormitory. This is the lock that makes
        # the overlap and occupancy checks reliable under contention.
        with transaction.atomic():
            Dormitory.objects.select_for_update().filter(
                pk=self.dormitory_id
            ).exists()
            self.full_clean()
            super().save(*args, **kwargs)

    def is_active_at(self, when=None):
        when = when or timezone.now()
        return bool(
            self.cancelled_at is None
            and self.starts_at <= when <= self.ends_at
        )

    @property
    def is_active(self):
        return self.is_active_at()

    def __str__(self):
        return f"{self.dormitory} +{self.additional_capacity} until {self.ends_at:%Y-%m-%d %H:%M}"


class StaffRole(models.TextChoices):
    """Institutional system user roles for Eden High School."""

    UNASSIGNED = "UNASSIGNED", "Unassigned"
    ADMIN = "ADMIN", "System Administrator"
    SENIOR_HOUSEMASTER = "SENIOR_HOUSEMASTER", "Senior Housemaster"
    HOUSEMASTER = "HOUSEMASTER", "Housemaster / Housemistress"
    HOUSE_TUTOR = "HOUSE_TUTOR", "House Tutor"
    GATE_OFFICER = "GATE_OFFICER", "Security / Gate Officer"
    MEDICAL = "MEDICAL", "Medical / Infirmary Lead"


class UserProfile(BaseModel):
    """
    Profile extension linking auth.User to their institutional role
    and operational boundaries in Eden High School.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
        help_text="Underlying authentication user account.",
    )
    role = models.CharField(
        max_length=30,
        choices=StaffRole.choices,
        default=StaffRole.UNASSIGNED,
        help_text="Institutional operational role governing permissions.",
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
            hasattr(self.user, "housemaster")
            and self.user.housemaster
            and self.user.housemaster.is_senior_housemaster
        )

    @property
    def is_housemaster(self) -> bool:
        return self.role in (
            StaffRole.HOUSEMASTER,
            StaffRole.SENIOR_HOUSEMASTER,
        ) or hasattr(self.user, "housemaster")

    @property
    def is_gate_officer(self) -> bool:
        return self.role == StaffRole.GATE_OFFICER or self.is_admin

    @property
    def is_medical(self) -> bool:
        return self.role == StaffRole.MEDICAL

    def can_approve_exeat(self, exeat) -> bool:
        """
        Determines whether this user has authorization to approve or reject
        a student's exeat leave request.

        Medical leads are deliberately scoped to medical passes; they must never
        inherit the broad house-management permissions by being assigned an
        ADMIN or HOUSEMASTER role as a workaround. General emergency departures
        remain with the Headmaster/ADMIN and Senior Housemaster (D2).
        """
        if self.is_admin or self.is_senior_housemaster:
            return True
        if self.role == StaffRole.MEDICAL:
            return exeat.exeat_type == Exeat.ExeatType.MEDICAL
        if hasattr(self.user, "housemaster") and self.user.housemaster:
            hm = self.user.housemaster
            if hm.is_senior_housemaster:
                return True
            if hm.house and exeat.student and exeat.student.house_id == hm.house_id:
                return True
        # Backwards-compatibility fallback: lookup by staff_id or username
        hm = HouseMaster.objects.filter(
            models.Q(staff_id__iexact=self.user.username)
            | models.Q(full_name__icontains=self.user.username)
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
        if (
            self.is_admin
            or self.is_senior_housemaster
            or self.role == StaffRole.HOUSE_TUTOR
        ):
            return True
        if hasattr(self.user, "housemaster"):
            return True
        return False

    def can_log_gate_movement(self) -> bool:
        """
        System administrators and staff holding the Gate Officer role can log
        gate movements. Shift/duty assignment enforcement is applied separately
        by ``core.permissions.can_log_gate_check`` when the school enables it.
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
        help_text="Login user account associated with this housemaster profile.",
    )
    full_name = models.CharField(max_length=150)
    staff_id = models.CharField(max_length=30, unique=True)
    phone_number = models.CharField(max_length=17)

    house = models.ForeignKey(
        House,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="housemasters",
        help_text="House assigned to this master. Senior Housemaster can be left blank for school-wide oversight.",
    )
    assigned_dormitory = models.ForeignKey(
        Dormitory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Optional specific dormitory block assigned to this housemaster.",
    )
    is_senior_housemaster = models.BooleanField(
        default=False,
        help_text="Designates the Senior Housemaster. Only one staff member can hold this title.",
    )

    class Meta:
        ordering = ["full_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["is_senior_housemaster"],
                condition=models.Q(is_senior_housemaster=True),
                name="unique_senior_housemaster",
            )
        ]

    def clean(self):
        super().clean()
        # 1. Enforce that regular Housemasters MUST be assigned to a House
        if not self.is_senior_housemaster and self.house is None:
            raise ValidationError(
                {"house": "Regular Housemasters must be assigned to a specific House."}
            )

        # 2. Guarantee only one Senior Housemaster exists across the institution
        if self.is_senior_housemaster:
            existing = HouseMaster.objects.filter(is_senior_housemaster=True).exclude(
                pk=self.pk
            )
            if existing.exists():
                raise ValidationError(
                    {
                        "is_senior_housemaster": f"A Senior Housemaster already exists ({existing.first().full_name}). "
                        f"Unmark the active Senior Housemaster before assigning a new one."
                    }
                )

        # 3. Ensure assigned dormitory belongs to the selected House (if both are selected)
        if (
            self.assigned_dormitory
            and self.house
            and self.assigned_dormitory.house_id != self.house_id
        ):
            raise ValidationError(
                {
                    "assigned_dormitory": f"Dormitory '{self.assigned_dormitory.name}' does not belong to {self.house.name}."
                }
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
        if self.user:
            profile, _ = UserProfile.objects.get_or_create(user=self.user)
            target_role = (
                StaffRole.SENIOR_HOUSEMASTER
                if self.is_senior_housemaster
                else StaffRole.HOUSEMASTER
            )
            if (
                profile.role
                in (
                    StaffRole.UNASSIGNED,
                    StaffRole.HOUSEMASTER,
                    StaffRole.SENIOR_HOUSEMASTER,
                )
                and profile.role != target_role
            ):
                profile.role = target_role
                profile.save(update_fields=["role"])

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
        COMPLETED = "Completed", "Completed"

    admission_no = models.CharField(max_length=30, unique=True, db_index=True)
    index_number = models.CharField(max_length=20, unique=True, db_index=True)
    public_token = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        help_text="Opaque identifier used only for the public student ID QR check.",
    )
    public_token_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When set, the public QR link stops working at this time. "
            "Leave empty for a link that never expires."
        ),
    )
    full_name = models.CharField(max_length=200)
    gender = models.CharField(max_length=10, choices=Gender.choices)
    date_of_birth = models.DateField(null=True, blank=True)
    photo = models.ImageField(
        storage=PrivateStudentPhotoStorage(),
        upload_to=student_photo_upload_to,
        blank=True,
        validators=[
            FileExtensionValidator(allowed_extensions=["jpg", "jpeg", "png"]),
            validate_student_photo,
        ],
        help_text=(
            "Optional portrait photo. JPEG or PNG, maximum 5 MB and 25 megapixels."
        ),
    )

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
    room = models.ForeignKey(
        DormitoryRoom,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="students",
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
    completed_on = models.DateField(null=True, blank=True)
    completion_academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="completed_students",
    )
    expected_completion_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="students_expected_to_complete",
    )

    class Meta:
        ordering = ["full_name"]

    def clean(self):
        super().clean()
        if self.status == self.EnrollmentStatus.COMPLETED and (
            not self.completed_on or not self.completion_academic_year_id
        ):
            raise ValidationError(
                "Completed students must have a completion date and academic year."
            )
        if self.status == self.EnrollmentStatus.COMPLETED and (
            not self.pk
            or not self.progression_records.filter(
                event=StudentProgressionRecord.Event.PHASED_OUT,
                academic_year_id=self.completion_academic_year_id,
            ).exists()
        ):
            raise ValidationError(
                "Students can only be completed through the audited phase-out process."
            )
        if self.residency == self.ResidencyStatus.DAY and self.dormitory is not None:
            raise ValidationError(
                {"dormitory": "Day students cannot be assigned to a dormitory block."}
            )
        if self.residency == self.ResidencyStatus.DAY and self.room is not None:
            raise ValidationError(
                {"room": "Day students cannot be assigned to a dormitory room."}
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
        if self.room and self.room.dormitory_id != self.dormitory_id:
            raise ValidationError(
                {"room": "The selected room must belong to the assigned dormitory."}
            )
        if self.room and self.status != self.EnrollmentStatus.COMPLETED:
            room_occupancy = Student.objects.exclude(
                status=self.EnrollmentStatus.COMPLETED
            ).filter(
                room_id=self.room_id,
                residency=self.ResidencyStatus.BOARDING,
            )
            if self.pk:
                room_occupancy = room_occupancy.exclude(pk=self.pk)
            if room_occupancy.count() >= self.room.capacity:
                raise ValidationError(
                    {"room": f"{self.room} is at its capacity of {self.room.capacity}."}
                )
        if self.dormitory and self.status != self.EnrollmentStatus.COMPLETED:
            # Dormitory capacity is the safety limit; room capacity alone is
            # not enough (F-13). A current senior exception may add a strictly
            # time-bound number of extra places.
            dormitory_occupancy = Student.objects.exclude(
                status=self.EnrollmentStatus.COMPLETED
            ).filter(
                dormitory_id=self.dormitory_id,
                residency=self.ResidencyStatus.BOARDING,
            )
            if self.pk:
                dormitory_occupancy = dormitory_occupancy.exclude(pk=self.pk)
            now = timezone.now()
            exception_places = sum(
                DormitoryCapacityException.objects.filter(
                    dormitory_id=self.dormitory_id,
                    cancelled_at__isnull=True,
                    starts_at__lte=now,
                    ends_at__gte=now,
                ).values_list("additional_capacity", flat=True)
            )
            allowed = self.dormitory.capacity + exception_places
            if dormitory_occupancy.count() >= allowed:
                raise ValidationError(
                    {
                        "dormitory": (
                            f"{self.dormitory} is at its capacity of "
                            f"{self.dormitory.capacity}. A time-bound senior "
                            "capacity exception is required before placing "
                            "another boarder."
                        )
                    }
                )
        if (
            self.class_group
            and self.class_group.programme_id
            and self.programme_id != self.class_group.programme_id
        ):
            raise ValidationError(
                {"class_group": "The selected class belongs to a different programme."}
            )
        if self.dormitory:
            if self.dormitory.gender != DORMITORY_GENDER_BY_STUDENT_GENDER.get(
                self.gender
            ):
                raise ValidationError(
                    {
                        "dormitory": f"Gender Mismatch! Cannot place a {self.get_gender_display()} student in a "
                        f"{self.dormitory.get_gender_display()}."
                    }
                )

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if (
                not self.expected_completion_year_id
                and self.status == self.EnrollmentStatus.ENROLLED
                and self.level == AcademicTerm.LevelChoice.SHS_3
            ):
                self.expected_completion_year = AcademicYear.objects.filter(
                    is_current=True
                ).first()
            if self.room_id:
                # Lock the target room so concurrent saves cannot overfill it.
                DormitoryRoom.objects.select_for_update().filter(
                    pk=self.room_id
                ).exists()
            if self.dormitory_id:
                # Serialize dormitory-capacity checks with other placements and
                # with creation/cancellation of senior capacity exceptions.
                Dormitory.objects.select_for_update().filter(
                    pk=self.dormitory_id
                ).exists()
                DormitoryCapacityException.objects.select_for_update().filter(
                    dormitory_id=self.dormitory_id,
                    cancelled_at__isnull=True,
                    starts_at__lte=timezone.now(),
                    ends_at__gte=timezone.now(),
                ).exists()
            previous_status = None
            if not self._state.adding:
                previous_status = (
                    Student.objects.filter(pk=self.pk)
                    .values_list("status", flat=True)
                    .first()
                )
            if (
                self.status == self.EnrollmentStatus.ENROLLED
                and previous_status != self.EnrollmentStatus.ENROLLED
            ):
                from tenancy.entitlements import ensure_quota_available, quota_guard

                # Take the tenant quota lock for the surrounding transaction so
                # this check and the insert below cannot race another enrollment.
                with quota_guard("max_students"):
                    ensure_quota_available(
                        "max_students",
                        Student.objects.filter(
                            status=self.EnrollmentStatus.ENROLLED
                        ).count(),
                    )
            self.full_clean()
            super().save(*args, **kwargs)

    @property
    def public_token_expired(self):
        """True when the public QR link has an expiry that has already passed."""
        return bool(
            self.public_token_expires_at
            and timezone.now() >= self.public_token_expires_at
        )

    @property
    def level(self):
        """Extracts level string (SHS1, SHS2, SHS3) from assigned class group name."""
        if self.class_group and self.class_group.name:
            name = self.class_group.name.strip().upper()
            if name.startswith("1"):
                return AcademicTerm.LevelChoice.SHS_1
            elif name.startswith("2"):
                return AcademicTerm.LevelChoice.SHS_2
            elif name.startswith("3"):
                return AcademicTerm.LevelChoice.SHS_3
        return AcademicTerm.LevelChoice.ALL

    def __str__(self):
        return f"{self.full_name} ({self.admission_no})"


class StudentProgressionRecord(BaseModel):
    """Auditable class promotion or end-of-school completion event."""

    class Event(models.TextChoices):
        PROMOTED = "promoted", "Promoted"
        PHASED_OUT = "phased_out", "Completed SHS 3"

    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        related_name="progression_records",
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.PROTECT,
        related_name="student_progression_records",
    )
    event = models.CharField(max_length=20, choices=Event.choices)
    from_class_group = models.ForeignKey(
        ClassGroup,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="progressions_from",
    )
    to_class_group = models.ForeignKey(
        ClassGroup,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="progressions_to",
    )
    effective_on = models.DateField()
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="student_progression_actions",
    )

    class Meta:
        ordering = ["-effective_on", "student__full_name"]
        indexes = [models.Index(fields=["student", "academic_year"])]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "academic_year", "event"],
                name="unique_student_progression_event_per_year",
            )
        ]

    def __str__(self):
        return f"{self.student} - {self.get_event_display()} ({self.academic_year})"


# Single source of truth for matching a student's gender to a dormitory block.
# Student.clean(), the registration form, and the dependent-options view all use
# this so the offered choices and the validation can never disagree.
DORMITORY_GENDER_BY_STUDENT_GENDER = {
    Student.Gender.MALE: Dormitory.GenderBlock.MALE,
    Student.Gender.FEMALE: Dormitory.GenderBlock.FEMALE,
}


class Exeat(BaseModel):
    """Core Exeat request and approval lifecycle (Housemaster & Student Workflow)."""

    class ExeatType(models.TextChoices):
        MEDICAL = "Medical", "Medical Exeat"
        TOWN = "Town", "Town Day Exeat"
        WEEKEND = "Weekend", "Weekend Exeat"
        EMERGENCY = "Emergency", "Emergency / Personal Exeat"
        VACATION = "Vacation", "End of Term / Vacation Exeat"

    class Status(models.TextChoices):
        PENDING = "Pending", "Pending Approval"
        APPROVED = "Approved", "Approved (Pass Issued)"
        REJECTED = "Rejected", "Rejected"
        CANCELLED = "Cancelled", "Cancelled"

    exeat_number = models.CharField(
        max_length=30,
        unique=True,
        editable=False,
        help_text="Auto-generated exeat reference number e.g. EXT-20261004-A1B2",
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="exeats",
        help_text="Student requesting leave.",
    )
    academic_term = models.ForeignKey(
        AcademicTerm,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="exeats",
    )
    exeat_type = models.CharField(
        max_length=20, choices=ExeatType.choices, default=ExeatType.MEDICAL
    )
    reason = models.TextField(help_text="Detailed reason for leave request.")
    destination = models.CharField(
        max_length=200, help_text="Destination address or town during leave."
    )

    # Scheduled Travel Timestamps
    expected_departure = models.DateTimeField(
        help_text="Scheduled departure date & time."
    )
    expected_return = models.DateTimeField(help_text="Scheduled return date & time.")

    # Status & Approvals
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    approved_by = models.ForeignKey(
        HouseMaster,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_exeats",
        help_text="Housemaster / Senior Housemaster who signed off this pass (Approved only).",
    )
    approved_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_exeats_by_user",
        help_text=(
            "Authenticated authorizer for medical or emergency overrides where "
            "the approver does not hold a HouseMaster profile."
        ),
    )
    decided_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Immutable timestamp when the request was approved, rejected, or cancelled.",
    )
    is_emergency_override = models.BooleanField(
        default=False,
        help_text="True when approval used the documented emergency-override path.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="drafted_exeats",
        help_text="Staff member who originally drafted / submitted this leave request (audit trail).",
    )
    approval_remarks = models.TextField(
        blank=True, null=True, help_text="Housemaster's notes or conditions."
    )
    is_active = models.BooleanField(
        default=False,
        db_index=True,
        help_text=(
            "True while this Approved pass has no recorded return. "
            "Enforced by a per-student partial unique constraint."
        ),
    )

    class Meta:
        ordering = ["-expected_departure"]
        verbose_name = "Exeat / Leave Pass"
        verbose_name_plural = "Exeats / Leave Passes"
        indexes = [
            models.Index(fields=["exeat_number"]),
            models.Index(fields=["status", "expected_return"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["student"],
                condition=models.Q(is_active=True),
                name="unique_active_exeat_per_student",
            )
        ]

    def clean(self):
        super().clean()
        if self._state.adding:
            from tenancy.entitlements import feature_enabled

            if not feature_enabled("exeat_gate"):
                raise ValidationError(
                    "This tenant's subscription does not include exeat management."
                )
        # 1. Day students validation
        if (
            self.student_id
            and getattr(self, "student", None)
            and self.student.residency == Student.ResidencyStatus.DAY
        ):
            raise ValidationError(
                {"student": "Day students do not require boarding exeat passes."}
            )
        if (
            self.student_id
            and getattr(self, "student", None)
            and self.student.status == Student.EnrollmentStatus.COMPLETED
        ):
            raise ValidationError(
                {"student": "Completed students cannot receive new exeat passes."}
            )

        # 2. Date order validation
        if self.expected_departure and self.expected_return:
            if self.expected_return <= self.expected_departure:
                raise ValidationError(
                    {
                        "expected_return": "Expected return time must be after expected departure time."
                    }
                )

        # 3. Require an authorizer when status is Approved. A HouseMaster
        # profile is preferred for ordinary leave; a named user is allowed for
        # medical/emergency override paths where the authorizer has no
        # HouseMaster record.
        if self.status == self.Status.APPROVED and not (
            self.approved_by_id or self.approved_by_user_id
        ):
            raise ValidationError(
                {
                    "approved_by": (
                        "An approving Housemaster or named authorizer must be "
                        "specified when issuing an exeat pass."
                    )
                }
            )

        # 3b. General emergency passes must use the documented override path so
        # the immediate reason and the 24-hour post-hoc review always exist (D2).
        if (
            self.status == self.Status.APPROVED
            and self.exeat_type == self.ExeatType.EMERGENCY
            and not self.is_emergency_override
        ):
            raise ValidationError(
                {
                    "is_emergency_override": (
                        "A general emergency pass must be approved through the "
                        "emergency override workflow so the reason and 24-hour "
                        "review are recorded."
                    )
                }
            )

        # 4. Jurisdiction guard: the signatory Housemaster must actually have authority over this student's house.
        if (
            self.status == self.Status.APPROVED
            and self.approved_by_id
            and self.student_id
        ):
            hm = self.approved_by
            if not hm.is_senior_housemaster:
                student_house_id = (
                    self.student.house_id
                    if getattr(self.student, "house_id", None)
                    else None
                )
                if hm.house_id is None:
                    # Non-senior Housemaster with no assigned house cannot approve anything
                    raise ValidationError(
                        {
                            "approved_by": f"{hm.full_name} is not assigned to a House and is not the Senior Housemaster. "
                            f"Only the student's Housemaster or the Senior Housemaster can approve this pass."
                        }
                    )
                if student_house_id is not None and hm.house_id != student_house_id:
                    raise ValidationError(
                        {
                            "approved_by": f"Jurisdiction error: {hm.full_name} is the Housemaster for {hm.house.name}, "
                            f"but this student belongs to {self.student.house.name if self.student.house else 'an unassigned House'}. "
                            f"Approval is restricted to the student's own Housemaster or the Senior Housemaster."
                        }
                    )

        # 4b. Named-user authorizers are checked against their scoped role.
        if (
            self.status == self.Status.APPROVED
            and self.approved_by_user_id
            and not self.approved_by_id
            and self.student_id
        ):
            profile = get_user_profile(self.approved_by_user)
            if profile is None or not profile.can_approve_exeat(self):
                raise ValidationError(
                    {
                        "approved_by_user": (
                            "This user is not authorized to approve this exeat. "
                            "Medical leads may approve medical passes only; ordinary "
                            "and general emergency passes require the student's "
                            "Housemaster, the Senior Housemaster, or an administrator."
                        )
                    }
                )

        # 5. One active (Approved and unreturned) pass per student (F-02).
        # The database partial unique constraint is the final guard for
        # concurrent approvals; this check provides a clear, actionable error.
        if self.status == self.Status.APPROVED and self.student_id:
            conflicting = Exeat.objects.filter(
                student_id=self.student_id,
                is_active=True,
            )
            if self.pk:
                conflicting = conflicting.exclude(pk=self.pk)
            conflict = conflicting.order_by("expected_departure").first()
            if conflict is not None:
                raise ValidationError(
                    {
                        "status": (
                            "This student already has an active approved pass "
                            f"({conflict.exeat_number}). Record the return or "
                            "cancel that pass before approving another."
                        )
                    }
                )

    def _compute_is_active(self):
        """Return whether this pass is Approved with no recorded return."""
        if self.status != self.Status.APPROVED or not self.pk:
            return False

        reconciliation = (
            ExeatReconciliation.objects.filter(exeat_id=self.pk)
            .only("outcome", "actual_return")
            .first()
        )
        if reconciliation is not None:
            return bool(
                reconciliation.outcome == ExeatReconciliation.Outcome.STUDENT_OUT
                and reconciliation.actual_return is None
            )

        movement = (
            ExeatMovement.objects.filter(exeat_id=self.pk)
            .only("actual_return")
            .first()
        )
        return not (movement is not None and movement.actual_return is not None)

    @classmethod
    def refresh_active_state(cls, exeat_id):
        """Recompute and persist one pass's active flag after a movement change."""
        if not exeat_id:
            return None
        exeat = cls.objects.filter(pk=exeat_id).first()
        if exeat is None:
            return None
        expected_state = exeat._compute_is_active()
        if exeat.is_active != expected_state:
            cls.objects.filter(pk=exeat_id).update(
                is_active=expected_state,
                updated_at=timezone.now(),
            )
            exeat.is_active = expected_state
        return exeat

    def save(self, *args, **kwargs):
        # 1. Auto-generate unique Exeat Pass Number on creation
        if not self.exeat_number:
            timestamp_str = timezone.now().strftime("%Y%m%d")
            random_uuid = str(self.id)[:8].upper()
            self.exeat_number = f"EXT-{timestamp_str}-{random_uuid}"

        # 2. Auto-assign active term matching the student's form level
        if not self.academic_term_id and self.student_id:
            student_level = self.student.level
            current_term = (
                AcademicTerm.objects.filter(
                    is_current=True,
                    level__in=[student_level, AcademicTerm.LevelChoice.ALL],
                )
                .order_by("-created_at")
                .first()
            )

            if current_term:
                self.academic_term = current_term

        # Record the decision time once. Reports must not use updated_at,
        # which moves whenever an unrelated field is edited later (F-20).
        if (
            self.status in (self.Status.APPROVED, self.Status.REJECTED, self.Status.CANCELLED)
            and self.decided_at is None
        ):
            self.decided_at = timezone.now()

        # Keep the database guard in sync with the request state before the
        # model constraint validation runs.
        self.is_active = self._compute_is_active()
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def approver_display(self):
        """Return the authorizer for display, supporting both approval paths."""
        if self.approved_by_id:
            return self.approved_by.full_name
        if self.approved_by_user_id:
            return (
                self.approved_by_user.get_full_name()
                or self.approved_by_user.get_username()
            )
        return "—"

    @property
    def is_approved(self):
        return self.status == self.Status.APPROVED

    @property
    def wait_hours(self):
        """Hours elapsed since this exeat request was created."""
        if not self.created_at:
            return 0
        delta = timezone.now() - self.created_at
        return max(0, delta.total_seconds() / 3600)

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
        related_name="movement",
        help_text="Associated approved exeat pass.",
    )
    actual_departure = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when student exited the school gate.",
    )
    actual_return = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when student returned through the gate.",
    )
    departure_recorded_by = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Gate officer or master name/ID snapshot for display.",
    )
    return_recorded_by = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Gate officer or master name/ID snapshot for display.",
    )
    departure_recorded_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="exeat_departures_recorded",
        help_text="Authenticated staff account that recorded the departure.",
    )
    return_recorded_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="exeat_returns_recorded",
        help_text="Authenticated staff account that recorded the return.",
    )

    class Meta:
        verbose_name = "Exeat Gate Log"
        verbose_name_plural = "Exeat Gate Logs"

    def clean(self):
        super().clean()
        # 1. Gate movement can only be created for APPROVED exeats
        if self.exeat_id and self.exeat.status != Exeat.Status.APPROVED:
            raise ValidationError(
                {
                    "exeat": f"Cannot record gate movements for exeat pass '{self.exeat.exeat_number}' because it is {self.exeat.get_status_display()}."
                }
            )

        # 2. Return timestamp must be after departure timestamp
        if self.actual_departure and self.actual_return:
            if self.actual_return <= self.actual_departure:
                raise ValidationError(
                    {
                        "actual_return": "Actual return time must be after actual departure time."
                    }
                )

        # 3. Cannot log return before departure is logged
        if self.actual_return and not self.actual_departure:
            raise ValidationError(
                {
                    "actual_return": "Cannot log return timestamp before actual departure has been logged."
                }
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
        # A recorded return releases the student's single active pass.
        Exeat.refresh_active_state(self.exeat_id)

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


class MovementConcernReport(BaseModel):
    """A public QR-scan report for school staff to review."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        REVIEWED = "reviewed", "Reviewed"

    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        related_name="movement_concern_reports",
    )
    exeat = models.ForeignKey(
        Exeat,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="movement_concern_reports",
    )
    details = models.TextField(
        validators=[MaxLengthValidator(1000)],
        help_text="Optional details supplied by the person scanning the QR code.",
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.OPEN,
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Public Movement Concern Report"
        verbose_name_plural = "Public Movement Concern Reports"

    def __str__(self):
        return f"Movement concern for {self.student} ({self.get_status_display()})"


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
    reason = models.TextField(
        help_text="Evidence or explanation for reconciling missing gate records."
    )
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

        movement = (
            ExeatMovement.objects.filter(exeat_id=self.exeat_id).first()
            if self.exeat_id
            else None
        )
        if movement and (movement.actual_departure or movement.actual_return):
            raise ValidationError(
                {"exeat": "A pass with recorded gate movement cannot be reconciled."}
            )

        if self.outcome == self.Outcome.UNUSED:
            if self.actual_departure or self.actual_return:
                raise ValidationError(
                    {"outcome": "An unused pass cannot have travel timestamps."}
                )
        elif self.outcome == self.Outcome.STUDENT_OUT:
            if not self.actual_departure:
                raise ValidationError(
                    {
                        "actual_departure": "Record the confirmed or best-known departure time."
                    }
                )
            if self.actual_return:
                raise ValidationError(
                    {"actual_return": "A student still out cannot have a return time."}
                )
        elif self.outcome == self.Outcome.RETURNED:
            if not self.actual_departure:
                raise ValidationError(
                    {
                        "actual_departure": "Record the confirmed or best-known departure time."
                    }
                )
            if not self.actual_return:
                raise ValidationError(
                    {"actual_return": "Record the confirmed or best-known return time."}
                )

        if (
            self.actual_departure
            and self.actual_return
            and self.actual_return <= self.actual_departure
        ):
            raise ValidationError(
                {"actual_return": "Actual return must be after actual departure."}
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
        # UNUSED/RETURNED reconciliations release the student; STUDENT_OUT
        # keeps the pass active until a return is recorded.
        Exeat.refresh_active_state(self.exeat_id)

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
        return (
            f"Reconciliation: {self.exeat.exeat_number} ({self.get_outcome_display()})"
        )


class ExeatScheduleChange(BaseModel):
    """Append-only record of a formal change to an exeat's travel window."""

    class ChangeType(models.TextChoices):
        RESCHEDULE = "reschedule", "Rescheduled by authorized staff"
        EARLY_DEPARTURE = "early_departure", "Approved early departure"

    exeat = models.ForeignKey(
        Exeat,
        on_delete=models.CASCADE,
        related_name="schedule_changes",
    )
    change_type = models.CharField(
        max_length=20,
        choices=ChangeType.choices,
        default=ChangeType.RESCHEDULE,
    )
    previous_departure = models.DateTimeField()
    previous_return = models.DateTimeField()
    new_departure = models.DateTimeField()
    new_return = models.DateTimeField()
    reason = models.TextField()
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="exeat_schedule_changes",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Exeat Schedule Change"
        verbose_name_plural = "Exeat Schedule Changes"

    def clean(self):
        super().clean()
        if not (self.reason or "").strip():
            raise ValidationError({"reason": "Record why the schedule changed."})
        if self.new_departure and self.new_return:
            if self.new_return <= self.new_departure:
                raise ValidationError(
                    {"new_return": "The new return time must be after departure."}
                )
        if self.previous_departure and self.previous_return:
            if self.previous_return <= self.previous_departure:
                raise ValidationError(
                    {"previous_return": "The previous schedule is invalid."}
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.exeat.exeat_number}: {self.previous_departure} → {self.new_departure}"


class ExeatReview(BaseModel):
    """Post-hoc review item for an emergency override or after-hours medical approval."""

    class ReviewType(models.TextChoices):
        EMERGENCY_OVERRIDE = "emergency_override", "Emergency override"
        MEDICAL_AFTER_HOURS = "medical_after_hours", "After-hours medical approval"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending review"
        REVIEWED = "reviewed", "Reviewed"
        ESCALATED = "escalated", "Escalated"

    class Outcome(models.TextChoices):
        UPHELD = "upheld", "Upheld - the emergency action was justified"
        ESCALATED = "escalated", "Escalated for further action"
        REJECTED = "rejected", "Not justified - follow-up required"

    exeat = models.ForeignKey(
        Exeat,
        on_delete=models.CASCADE,
        related_name="reviews",
    )
    review_type = models.CharField(max_length=30, choices=ReviewType.choices)
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.PENDING,
    )
    reason = models.TextField(
        help_text="Immediate reason recorded by the authorizing staff member."
    )
    authorized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="authorized_exeat_reviews",
    )
    authorized_at = models.DateTimeField(default=timezone.now)
    due_at = models.DateTimeField()
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_exeat_reviews",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(max_length=12, choices=Outcome.choices, blank=True)
    review_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["due_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["exeat", "review_type"],
                name="unique_exeat_review_type",
            )
        ]
        verbose_name = "Exeat Review"
        verbose_name_plural = "Exeat Reviews"

    def clean(self):
        super().clean()
        if not (self.reason or "").strip():
            raise ValidationError(
                {"reason": "A reason is required for every emergency or after-hours approval."}
            )
        if self.authorized_at and self.due_at and self.due_at <= self.authorized_at:
            raise ValidationError({"due_at": "The review deadline must be in the future."})
        if self.status != self.Status.PENDING:
            if not self.reviewed_by_id or not self.reviewed_at or not self.outcome:
                raise ValidationError(
                    {
                        "status": (
                            "A completed review requires a reviewer, review time, "
                            "and outcome."
                        )
                    }
                )

    def save(self, *args, **kwargs):
        if not self.authorized_at:
            self.authorized_at = timezone.now()
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def is_overdue(self):
        return self.status == self.Status.PENDING and timezone.now() >= self.due_at

    def __str__(self):
        return f"{self.exeat.exeat_number} - {self.get_review_type_display()}"


class GateDutyAssignment(BaseModel):
    """Time-bound gate shift or temporary cover assignment."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="gate_duty_assignments",
    )
    gate_name = models.CharField(max_length=100, default="Main Gate")
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField()
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_gate_duty_assignments",
    )
    reason = models.TextField(
        blank=True,
        help_text="Optional note, especially for temporary cover.",
    )
    is_temporary = models.BooleanField(default=False)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cancelled_gate_duty_assignments",
    )

    class Meta:
        ordering = ["-starts_at", "user__username"]
        verbose_name = "Gate Duty Assignment"
        verbose_name_plural = "Gate Duty Assignments"

    def clean(self):
        super().clean()
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "The shift must end after it starts."})
        if not self.approved_by_id and not self.cancelled_at:
            raise ValidationError(
                {"approved_by": "A duty assignment requires an approving officer."}
            )
        if self.user_id and not self.cancelled_at:
            profile = get_user_profile(self.user)
            if profile is None or not profile.can_log_gate_movement():
                raise ValidationError(
                    {
                        "user": (
                            "Only a Gate Officer or administrator can hold a gate "
                            "duty assignment."
                        )
                    }
                )
        if self.approved_by_id and not self.cancelled_at:
            approver_profile = get_user_profile(self.approved_by)
            if approver_profile is None or not (
                approver_profile.is_admin
                or approver_profile.is_senior_housemaster
            ):
                raise ValidationError(
                    {
                        "approved_by": (
                            "Only the Headmaster/ADMIN or Senior Housemaster may "
                            "approve gate duty."
                        )
                    }
                )
        if self.user_id and self.starts_at and self.ends_at and not self.cancelled_at:
            overlapping = GateDutyAssignment.objects.filter(
                user_id=self.user_id,
                cancelled_at__isnull=True,
                starts_at__lt=self.ends_at,
                ends_at__gt=self.starts_at,
            )
            if self.pk:
                overlapping = overlapping.exclude(pk=self.pk)
            if overlapping.exists():
                raise ValidationError(
                    {
                        "starts_at": (
                            "This staff member already has an overlapping gate "
                            "assignment."
                        )
                    }
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def is_active_at(self, when=None):
        when = when or timezone.now()
        return bool(
            self.cancelled_at is None
            and self.starts_at <= when <= self.ends_at
        )

    @property
    def is_active(self):
        return self.is_active_at()

    def __str__(self):
        return f"{self.user} at {self.gate_name} ({self.starts_at:%Y-%m-%d %H:%M})"


class LateReturnIncident(BaseModel):
    """Investigation trail for a student who returned after the expected time."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        INVESTIGATING = "investigating", "Investigating"
        RESOLVED = "resolved", "Resolved"
        CLOSED = "closed", "Closed"

    class Escalation(models.IntegerChoices):
        HOUSEMASTER = 1, "Housemaster"
        SENIOR_HOUSEMASTER = 2, "Senior Housemaster"
        HEADMASTER = 3, "Headmaster"

    exeat = models.OneToOneField(
        Exeat,
        on_delete=models.CASCADE,
        related_name="late_return_incident",
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="late_return_incidents",
    )
    expected_return = models.DateTimeField()
    detected_at = models.DateTimeField(default=timezone.now)
    actual_return = models.DateTimeField(null=True, blank=True)
    escalation_level = models.PositiveSmallIntegerField(
        choices=Escalation.choices,
        default=Escalation.HOUSEMASTER,
    )
    owner = models.ForeignKey(
        HouseMaster,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="late_return_incidents",
    )
    status = models.CharField(
        max_length=15,
        choices=Status.choices,
        default=Status.OPEN,
    )
    summary = models.TextField(blank=True)
    resolution = models.TextField(blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_late_return_incidents",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    last_notified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-detected_at"]
        verbose_name = "Late Return Incident"
        verbose_name_plural = "Late Return Incidents"

    def clean(self):
        super().clean()
        if self.status in (self.Status.RESOLVED, self.Status.CLOSED):
            if not (self.resolution or "").strip():
                raise ValidationError(
                    {"resolution": "Record the investigation outcome before closing."}
                )
            if not self.resolved_by_id or not self.resolved_at:
                raise ValidationError(
                    {"status": "A closed incident requires a resolver and timestamp."}
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def delay_hours(self):
        if not self.expected_return:
            return 0
        end = self.actual_return or timezone.now()
        return round(max(0, (end - self.expected_return).total_seconds() / 3600), 1)

    def __str__(self):
        return f"Late return: {self.student.full_name} ({self.exeat.exeat_number})"


class StaffNotification(BaseModel):
    """Small in-app notification for operational escalations and review queues."""

    class Kind(models.TextChoices):
        LATE_RETURN = "late_return", "Late return"
        EXEAT_REVIEW = "exeat_review", "Exeat review"
        ROLL_CALL_CORRECTION = "roll_call_correction", "Roll-call correction"
        DISPUTE = "dispute", "Dispute"
        GENERAL = "general", "General"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="staff_notifications",
    )
    kind = models.CharField(max_length=30, choices=Kind.choices, default=Kind.GENERAL)
    title = models.CharField(max_length=150)
    message = models.TextField()
    url = models.CharField(max_length=500, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "read_at"])]
        verbose_name = "Staff Notification"
        verbose_name_plural = "Staff Notifications"

    def __str__(self):
        return f"{self.title} for {self.user}"


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
        local_enabled = (
            configuration.enabled
            if configuration is not None
            else getattr(settings, "SMS_ENABLED", False)
        )
        if not local_enabled:
            return False
        if settings.TENANCY_ENABLED:
            from tenancy.entitlements import feature_enabled

            return feature_enabled("sms_dispatch")
        return True

    def __str__(self):
        return "SMS Notifications"


class SchoolConfiguration(models.Model):
    """Singleton public-facing identity and contact details for the school."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    name = models.CharField(max_length=150, default="ATEBUBU SENIOR HIGH SCHOOL")
    short_name = models.CharField(
        max_length=50,
        default="ATSEC",
        blank=True,
        help_text="Short school name used in SMS notifications.",
    )
    logo = models.ImageField(upload_to=school_logo_upload_to, blank=True)
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


class ConfigurationAuditLog(models.Model):
    """Append-only record of privileged changes to system configuration."""

    class Event(models.TextChoices):
        ACTIVE_TERM_CHANGED = "active_term_changed", "Active academic term changed"
        ACADEMIC_TERM_CHANGED = "academic_term_changed", "Academic term updated"
        SMS_STATE_CHANGED = "sms_state_changed", "SMS notification state changed"
        SCHOOL_IDENTITY_CHANGED = "school_identity_changed", "School identity changed"
        STAFF_ROLE_CHANGED = "staff_role_changed", "Staff role changed"
        HOUSEMASTER_ASSIGNMENT_CHANGED = (
            "housemaster_assignment_changed",
            "Housemaster assignment changed",
        )
        OPERATIONAL_SETTINGS_CHANGED = (
            "operational_settings_changed",
            "Operational escalation settings changed",
        )

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="configuration_audit_events",
    )
    event = models.CharField(max_length=40, choices=Event.choices)
    description = models.CharField(max_length=255)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Configuration Audit Event"
        verbose_name_plural = "Configuration Audit Events"

    def __str__(self):
        return f"{self.get_event_display()} - {self.created_at:%Y-%m-%d %H:%M}"


class OperationalSettings(models.Model):
    """Tenant-level operational timing and escalation settings with safe defaults."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    roll_call_timeout_minutes = models.PositiveIntegerField(
        default=60,
        validators=[MinValueValidator(5), MaxValueValidator(240)],
        help_text="Minutes after a muster starts before it is marked expired.",
    )
    late_return_incident_after_minutes = models.PositiveIntegerField(
        default=120,
        validators=[MinValueValidator(15), MaxValueValidator(1440)],
        help_text="Minutes after expected return before a late-return incident opens.",
    )
    senior_housemaster_escalation_after_minutes = models.PositiveIntegerField(
        default=120,
        validators=[MinValueValidator(15), MaxValueValidator(1440)],
        help_text="Minutes after expected return before the Senior Housemaster is notified.",
    )
    headmaster_escalation_after_minutes = models.PositiveIntegerField(
        default=360,
        validators=[MinValueValidator(30), MaxValueValidator(2880)],
        help_text="Minutes after expected return before the Headmaster is notified.",
    )
    absence_follow_up_deadline_hours = models.PositiveIntegerField(
        default=24,
        validators=[MinValueValidator(1), MaxValueValidator(168)],
        help_text="Target time to review an unresolved absence.",
    )
    gate_duty_enforcement_enabled = models.BooleanField(
        default=False,
        help_text=(
            "When enabled, Gate Officers must have an active GateDutyAssignment "
            "to record movements. Keep disabled until the roster is populated."
        ),
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_operational_settings",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Operational Settings"
        verbose_name_plural = "Operational Settings"

    def clean(self):
        super().clean()
        if (
            self.senior_housemaster_escalation_after_minutes
            < self.late_return_incident_after_minutes
        ):
            raise ValidationError(
                {
                    "senior_housemaster_escalation_after_minutes": (
                        "The Senior Housemaster escalation cannot occur before "
                        "the late-return incident threshold."
                    )
                }
            )
        if (
            self.headmaster_escalation_after_minutes
            <= self.senior_housemaster_escalation_after_minutes
        ):
            raise ValidationError(
                {
                    "headmaster_escalation_after_minutes": (
                        "The Headmaster escalation must be later than the Senior "
                        "Housemaster escalation."
                    )
                }
            )

    def save(self, *args, **kwargs):
        self.pk = 1
        previous = (
            type(self).objects.filter(pk=1)
            .values(
                "roll_call_timeout_minutes",
                "late_return_incident_after_minutes",
                "senior_housemaster_escalation_after_minutes",
                "headmaster_escalation_after_minutes",
                "absence_follow_up_deadline_hours",
                "gate_duty_enforcement_enabled",
            )
            .first()
        )
        self.full_clean()
        super().save(*args, **kwargs)
        current = {
            "roll_call_timeout_minutes": self.roll_call_timeout_minutes,
            "late_return_incident_after_minutes": self.late_return_incident_after_minutes,
            "senior_housemaster_escalation_after_minutes": (
                self.senior_housemaster_escalation_after_minutes
            ),
            "headmaster_escalation_after_minutes": (
                self.headmaster_escalation_after_minutes
            ),
            "absence_follow_up_deadline_hours": self.absence_follow_up_deadline_hours,
            "gate_duty_enforcement_enabled": self.gate_duty_enforcement_enabled,
        }
        if previous is None:
            details = {"created": current}
        elif previous != current:
            details = {
                "before": {
                    key: str(value) for key, value in previous.items()
                },
                "after": {key: str(value) for key, value in current.items()},
            }
        else:
            return
        ConfigurationAuditLog.objects.create(
            actor=self.updated_by,
            event=ConfigurationAuditLog.Event.OPERATIONAL_SETTINGS_CHANGED,
            description="Operational escalation settings updated",
            details=details,
        )

    @classmethod
    def get_solo(cls):
        return cls.objects.filter(pk=1).first() or cls()

    def __str__(self):
        return "Operational Settings"


class AuditEvent(models.Model):
    """Append-only audit event for sensitive operational actions."""

    class Action(models.TextChoices):
        EXEAT_CANCELLED = "exeat_cancelled", "Exeat cancelled"
        EXEAT_RESCHEDULED = "exeat_rescheduled", "Exeat rescheduled"
        EMERGENCY_OVERRIDE = "emergency_override", "Emergency override used"
        EXEAT_REVIEW_DECIDED = "exeat_review_decided", "Exeat review decided"
        GATE_DUTY_CHANGED = "gate_duty_changed", "Gate duty assignment changed"
        LATE_RETURN_OPENED = "late_return_opened", "Late-return incident opened"
        LATE_RETURN_UPDATED = "late_return_updated", "Late-return incident updated"
        ROLL_CALL_CORRECTION_REQUESTED = (
            "roll_call_correction_requested",
            "Roll-call correction requested",
        )
        ROLL_CALL_CORRECTION_DECIDED = (
            "roll_call_correction_decided",
            "Roll-call correction decided",
        )
        DORMITORY_CAPACITY_EXCEPTION = (
            "dormitory_capacity_exception",
            "Dormitory capacity exception",
        )
        EXPORT = "export", "Sensitive report export"
        ADMIN_EDIT = "admin_edit", "Administrative edit"
        DISPUTE = "dispute", "Dispute case changed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    action = models.CharField(max_length=40, choices=Action.choices)
    target_type = models.CharField(max_length=80, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    summary = models.CharField(max_length=255)
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Audit Event"
        verbose_name_plural = "Audit Events"
        indexes = [
            models.Index(fields=["action", "created_at"]),
            models.Index(fields=["target_type", "target_id"]),
        ]

    @classmethod
    def record(cls, *, actor, action, summary, target_type="", target_id="", before=None, after=None, metadata=None):
        return cls.objects.create(
            actor=actor if getattr(actor, "is_authenticated", False) else None,
            action=action,
            target_type=target_type,
            target_id=str(target_id or "")[:64],
            summary=summary[:255],
            before=before or {},
            after=after or {},
            metadata=metadata or {},
        )

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Audit events are append-only and cannot be edited.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Audit events cannot be deleted.")

    def __str__(self):
        return f"{self.get_action_display()} - {self.created_at:%Y-%m-%d %H:%M}"


class DataExportLog(models.Model):
    """Audit record for a report or bulk-data export."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="data_export_logs",
    )
    report_name = models.CharField(max_length=150)
    file_format = models.CharField(max_length=10)
    filters = models.JSONField(default=dict, blank=True)
    row_count = models.PositiveIntegerField(default=0)
    purpose = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Data Export Log"
        verbose_name_plural = "Data Export Logs"

    def __str__(self):
        return f"{self.report_name} ({self.file_format}) by {self.actor}"


class StudentIDExport(BaseModel):
    """Tracked background export of active students' ID card images."""

    class Status(models.TextChoices):
        QUEUED = "Queued", "Queued"
        PROCESSING = "Processing", "Processing"
        COMPLETED = "Completed", "Completed"
        FAILED = "Failed", "Failed"

    class ImageFormat(models.TextChoices):
        PNG = "PNG", "PNG"
        JPEG = "JPEG", "JPEG"

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="student_id_exports",
    )
    layout = models.CharField(max_length=10)
    image_format = models.CharField(max_length=4, choices=ImageFormat.choices)
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.QUEUED,
    )
    total_students = models.PositiveIntegerField(default=0)
    processed_students = models.PositiveIntegerField(default=0)
    archive_name = models.CharField(max_length=64, blank=True)
    error_message = models.TextField(blank=True)
    expires_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Student ID image export"
        verbose_name_plural = "Student ID image exports"

    def save(self, *args, **kwargs):
        if self._state.adding:
            from tenancy.entitlements import feature_enabled

            if not feature_enabled("student_id_cards"):
                raise ValidationError(
                    "This tenant's subscription does not include student ID cards."
                )
        if not self.archive_name:
            timestamp = timezone.now().strftime("%Y%m%d-%H%M%S")
            self.archive_name = (
                f"student-id-export-{timestamp}-{uuid.uuid4().hex[:8]}.zip"
            )
        if not self.expires_at:
            self.expires_at = timezone.now() + timezone.timedelta(days=7)
        super().save(*args, **kwargs)


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
        EXPIRED = "Expired", "Expired - unfinished (records retained)"

    house = models.ForeignKey(
        House,
        on_delete=models.CASCADE,
        related_name="roll_call_sessions",
        help_text="House being inspected.",
    )
    dormitory = models.ForeignKey(
        Dormitory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="roll_call_sessions",
        help_text="Optional specific dormitory block (or all blocks in house if blank).",
    )
    academic_term = models.ForeignKey(
        AcademicTerm,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="roll_call_sessions",
    )
    session_type = models.CharField(
        max_length=20, choices=SessionType.choices, default=SessionType.EVENING
    )
    date = models.DateField(default=date.today)
    conducted_by = models.ForeignKey(
        HouseMaster,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conducted_roll_calls",
        help_text="Staff member conducting this roll call muster.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_roll_call_sessions",
        help_text="User who started this roll call session.",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.IN_PROGRESS
    )
    notes = models.TextField(
        blank=True, null=True, help_text="General inspection notes or observations."
    )

    class Meta:
        ordering = ["-date", "-created_at"]
        verbose_name = "Roll Call Session"
        verbose_name_plural = "Roll Call Sessions"
        constraints = [
            models.UniqueConstraint(
                fields=["house", "dormitory", "session_type", "date"],
                condition=models.Q(
                    status="In Progress",
                    dormitory__isnull=False,
                ),
                name="unique_in_progress_roll_call_session",
            ),
            # PostgreSQL 14 does not support NULLS NOT DISTINCT. A separate
            # constraint for the house-wide (dormitory IS NULL) case keeps the
            # documented PostgreSQL 14 minimum working.
            models.UniqueConstraint(
                fields=["house", "session_type", "date"],
                condition=models.Q(
                    status="In Progress",
                    dormitory__isnull=True,
                ),
                name="unique_in_progress_roll_call_house_wide",
            ),
        ]

    def clean(self):
        super().clean()
        if self._state.adding:
            from tenancy.entitlements import feature_enabled

            if not feature_enabled("roll_call"):
                raise ValidationError(
                    "This tenant's subscription does not include roll call."
                )

    def save(self, *args, **kwargs):
        if self._state.adding:
            self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        block = f" - {self.dormitory.name}" if self.dormitory else ""
        return f"{self.house.name}{block} ({self.get_session_type_display()} - {self.date})"

    @property
    def total_students(self):
        if hasattr(self, "roll_call_total_students"):
            return self.roll_call_total_students
        return self.entries.count()

    @property
    def present_count(self):
        if hasattr(self, "roll_call_present_count"):
            return self.roll_call_present_count
        return self.entries.filter(status=RollCallEntry.Status.PRESENT).count()

    @property
    def unmarked_count(self):
        if hasattr(self, "roll_call_unmarked_count"):
            return self.roll_call_unmarked_count
        return self.entries.filter(status=RollCallEntry.Status.UNMARKED).count()

    @property
    def accounted_count(self):
        return self.total_students - self.unmarked_count

    @property
    def absent_count(self):
        if hasattr(self, "roll_call_absent_count"):
            return self.roll_call_absent_count
        return self.entries.filter(status=RollCallEntry.Status.ABSENT).count()

    @property
    def on_exeat_count(self):
        if hasattr(self, "roll_call_on_exeat_count"):
            return self.roll_call_on_exeat_count
        return self.entries.filter(status=RollCallEntry.Status.ON_EXEAT).count()

    @property
    def infirmary_count(self):
        if hasattr(self, "roll_call_infirmary_count"):
            return self.roll_call_infirmary_count
        return self.entries.filter(status=RollCallEntry.Status.INFIRMARY).count()

    @property
    def attendance_rate(self):
        total = self.total_students
        if not total:
            return 0
        present = self.present_count
        return round((present / total) * 100, 1)

    @property
    def expires_at(self):
        if not self.created_at:
            return None
        configuration = (
            OperationalSettings.objects.filter(pk=1)
            .only("roll_call_timeout_minutes")
            .first()
        )
        timeout_minutes = (
            configuration.roll_call_timeout_minutes
            if configuration is not None
            else int(ROLL_CALL_SESSION_TIMEOUT.total_seconds() // 60)
        )
        return self.created_at + timedelta(minutes=timeout_minutes)

    @property
    def is_expired(self):
        if self.status == self.Status.EXPIRED:
            return True
        return (
            self.status == self.Status.IN_PROGRESS
            and self.expires_at is not None
            and timezone.now() >= self.expires_at
        )


class RollCallNotification(BaseModel):
    """Persistent in-app notification for an expired roll call session."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="roll_call_notifications",
    )
    title = models.CharField(max_length=150)
    message = models.TextField()
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} for {self.user}"


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
        RollCallSession, on_delete=models.CASCADE, related_name="entries"
    )
    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="roll_call_entries"
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.UNMARKED
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
        """Prevents modification of entries belonging to a closed session."""
        super().clean()
        # Allow initial creation (no pk yet) but block updates on completed
        # or expired sessions. Expired sessions retain their evidence and are
        # immutable until an audited correction workflow exists (F-12).
        if self.pk:
            from django.core.exceptions import ValidationError

            if self.session.status == RollCallSession.Status.COMPLETED:
                raise ValidationError(
                    "Cannot modify attendance records in a finalized roll call session. "
                    "Records are locked for audit integrity."
                )
            if self.session.is_expired:
                raise ValidationError(
                    "Cannot modify attendance records in an expired roll call session. "
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


class RollCallCorrection(BaseModel):
    """Authorized, audited correction to a closed or expired roll-call entry."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending approval"
        APPROVED = "approved", "Approved and applied"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    entry = models.ForeignKey(
        RollCallEntry,
        on_delete=models.CASCADE,
        related_name="corrections",
    )
    previous_status = models.CharField(
        max_length=20, choices=RollCallEntry.Status.choices
    )
    requested_status = models.CharField(
        max_length=20, choices=RollCallEntry.Status.choices
    )
    previous_remarks = models.CharField(max_length=200, blank=True)
    requested_remarks = models.CharField(max_length=200, blank=True)
    reason = models.TextField(
        help_text="Why the original attendance mark was wrong."
    )
    evidence = models.TextField(
        blank=True,
        help_text="Supporting evidence or verification for a sensitive correction.",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_roll_call_corrections",
    )
    requires_second_approval = models.BooleanField(default=False)
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.PENDING,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_roll_call_corrections",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["entry"],
                condition=models.Q(status="pending"),
                name="one_pending_roll_call_correction_per_entry",
            )
        ]
        verbose_name = "Roll Call Correction"
        verbose_name_plural = "Roll Call Corrections"

    def clean(self):
        super().clean()
        if self.previous_status == self.requested_status:
            raise ValidationError(
                {"requested_status": "Choose a different attendance status."}
            )
        if not (self.reason or "").strip():
            raise ValidationError({"reason": "Record the reason for the correction."})
        if self.requires_second_approval and not (self.evidence or "").strip():
            raise ValidationError(
                {
                    "evidence": (
                        "A sensitive correction (Present/Absent or conduct-linked) "
                        "requires supporting evidence."
                    )
                }
            )
        if self.status == self.Status.APPROVED and not self.applied_at:
            raise ValidationError(
                {"status": "An approved correction must record when it was applied."}
            )
        if self.status in (self.Status.APPROVED, self.Status.REJECTED):
            if not self.reviewed_by_id or not self.reviewed_at:
                raise ValidationError(
                    {"status": "A decided correction requires a reviewer and time."}
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.entry.student.full_name}: {self.previous_status} → {self.requested_status}"


class DisputeCase(BaseModel):
    """Formal review of an exeat, gate, or attendance decision (F-30)."""

    class Category(models.TextChoices):
        EXEAT = "exeat", "Exeat or gate decision"
        ATTENDANCE = "attendance", "Roll-call / attendance"
        OTHER = "other", "Other boarding record"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        UNDER_REVIEW = "under_review", "Under review"
        RESOLVED = "resolved", "Resolved"
        REJECTED = "rejected", "Not upheld"
        CLOSED = "closed", "Closed"

    class Outcome(models.TextChoices):
        UPHELD = "upheld", "Upheld"
        PARTIALLY_UPHELD = "partially_upheld", "Partially upheld"
        NOT_UPHELD = "not_upheld", "Not upheld"
        CORRECTED = "corrected", "Record corrected"

    reference = models.CharField(max_length=30, unique=True, editable=False, blank=True)
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="dispute_cases",
    )
    category = models.CharField(max_length=20, choices=Category.choices)
    exeat = models.ForeignKey(
        Exeat,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dispute_cases",
    )
    roll_call_entry = models.ForeignKey(
        RollCallEntry,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dispute_cases",
    )
    raised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="raised_dispute_cases",
    )
    details = models.TextField(
        help_text="What is disputed and what outcome the student/guardian is seeking."
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_dispute_cases",
    )
    outcome = models.CharField(max_length=20, choices=Outcome.choices, blank=True)
    decision_notes = models.TextField(blank=True)
    correction = models.ForeignKey(
        RollCallCorrection,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dispute_cases",
    )
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_dispute_cases",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Dispute Case"
        verbose_name_plural = "Dispute Cases"

    def clean(self):
        super().clean()
        if not (self.details or "").strip():
            raise ValidationError({"details": "Record what is being disputed."})
        if self.category == self.Category.ATTENDANCE and not self.roll_call_entry_id:
            raise ValidationError(
                {"roll_call_entry": "Attendance disputes must link to the roll-call entry."}
            )
        if self.category == self.Category.EXEAT and not self.exeat_id:
            raise ValidationError(
                {"exeat": "Exeat disputes must link to the relevant pass."}
            )
        if self.exeat_id and self.student_id and self.exeat.student_id != self.student_id:
            raise ValidationError({"exeat": "The exeat must belong to the selected student."})
        if (
            self.roll_call_entry_id
            and self.student_id
            and self.roll_call_entry.student_id != self.student_id
        ):
            raise ValidationError(
                {"roll_call_entry": "The roll-call entry must belong to the selected student."}
            )
        if self.status in (self.Status.RESOLVED, self.Status.REJECTED, self.Status.CLOSED):
            if not self.outcome:
                raise ValidationError({"outcome": "Record the review outcome."})
            if not (self.decision_notes or "").strip():
                raise ValidationError({"decision_notes": "Record the decision reasoning."})
            if not self.resolved_by_id or not self.resolved_at:
                raise ValidationError(
                    {"status": "A decided dispute requires a reviewer and decision time."}
                )

    def save(self, *args, **kwargs):
        if not self.reference:
            timestamp = timezone.now().strftime("%Y%m%d")
            self.reference = f"DSP-{timestamp}-{str(self.id)[:8].upper()}"
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.reference} - {self.student.full_name}"


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def ensure_user_profile(sender, instance, created, **kwargs):
    """Automatically provisions a UserProfile whenever a new User is registered."""
    if created:
        role = StaffRole.ADMIN if instance.is_superuser else StaffRole.UNASSIGNED
        UserProfile.objects.get_or_create(user=instance, defaults={"role": role})


@receiver(pre_save, sender=settings.AUTH_USER_MODEL)
def enforce_staff_account_quota(sender, instance, **kwargs):
    # Fixture loading (loaddata) and other raw saves must not be quota-checked.
    if kwargs.get("raw"):
        return
    if not instance.is_staff or not settings.TENANCY_ENABLED:
        return
    existing = sender.objects.filter(pk=instance.pk).first() if instance.pk else None
    if existing and existing.is_staff:
        return
    from tenancy.entitlements import ensure_quota_available, quota_guard

    # Defence in depth: CoreConfig also wraps User.save so this check and the
    # insert share one transaction-scoped advisory lock. Re-acquiring the same
    # transaction-level lock is re-entrant and therefore safe.
    with quota_guard("max_staff"):
        ensure_quota_available(
            "max_staff",
            sender.objects.filter(is_staff=True).count(),
        )


@receiver(post_delete, sender=HouseMaster)
def demote_profile_on_housemaster_delete(sender, instance, **kwargs):
    """Reset a retained housemaster role when its HouseMaster record is removed.

    ``HouseMaster.save`` keeps the linked ``UserProfile.role`` in sync, but a
    deleted HouseMaster previously left the elevated role behind.  Since
    ``SENIOR_HOUSEMASTER`` is in ``permissions.ADMIN_ROLES`` (school-wide
    scope), a departed or reassigned senior housemaster could keep broad
    access indefinitely.  Clearing the role here closes that gap.
    """
    if not instance.user_id:
        return
    UserProfile.objects.filter(
        user_id=instance.user_id,
        role__in=(StaffRole.HOUSEMASTER, StaffRole.SENIOR_HOUSEMASTER),
    ).update(role=StaffRole.UNASSIGNED)


def get_user_profile(user):
    """Safely retrieves or provisions a UserProfile for any authenticated user."""
    if not user or not user.is_authenticated:
        return None
    try:
        profile = UserProfile.objects.get(user=user)
    except UserProfile.DoesNotExist:
        role = StaffRole.ADMIN if user.is_superuser else StaffRole.UNASSIGNED
        profile, _ = UserProfile.objects.get_or_create(
            user=user, defaults={"role": role}
        )

    # Keep role in sync if linked to a HouseMaster
    hm = getattr(user, "housemaster", None)
    if hm and profile.role in (
        StaffRole.UNASSIGNED,
        StaffRole.HOUSEMASTER,
        StaffRole.SENIOR_HOUSEMASTER,
    ):
        target_role = (
            StaffRole.SENIOR_HOUSEMASTER
            if hm.is_senior_housemaster
            else StaffRole.HOUSEMASTER
        )
        if profile.role != target_role:
            profile.role = target_role
            profile.save(update_fields=["role"])

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
      * Medical/Infirmary Lead -> Medical passes only
      * Tutors / Gate Officers / any other role -> empty QuerySet

    Args:
        user: A Django auth User instance (typically request.user).

    Returns:
        QuerySet[Exeat]: scoped, select_related for list rendering.
    """
    base_qs = (
        Exeat.objects.filter(status=Exeat.Status.PENDING)
        .select_related(
            "student",
            "student__house",
            "student__dormitory",
            "academic_term",
            "created_by",
        )
        .order_by("-created_at")
    )

    if not user or not user.is_authenticated:
        return base_qs.none()

    # Superuser shortcut: full institutional authority
    if user.is_superuser:
        return base_qs

    profile = get_user_profile(user)

    # Role guard — only Admin / Senior HM / Housemaster can approve anything.
    # A Medical/Infirmary Lead is a scoped approver for medical passes.
    if not profile:
        return base_qs.none()
    if profile.role not in (
        StaffRole.ADMIN,
        StaffRole.SENIOR_HOUSEMASTER,
        StaffRole.HOUSEMASTER,
        StaffRole.MEDICAL,
    ):
        return base_qs.none()

    # A Medical/Infirmary Lead sees only medical requests. General emergency
    # departures stay with the Headmaster/ADMIN and Senior Housemaster.
    if profile.role == StaffRole.MEDICAL:
        return base_qs.filter(exeat_type=Exeat.ExeatType.MEDICAL)

    # ADMIN and SENIOR_HOUSEMASTER: school-wide
    if profile.role in (StaffRole.ADMIN, StaffRole.SENIOR_HOUSEMASTER):
        return base_qs

    # HOUSEMASTER: only their directly assigned house
    hm = getattr(user, "housemaster", None)

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
    from django.db.models import Min, Count, F, Q as _Q
    from django.utils import timezone

    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    stats = {
        "total_pending": scoped_pending_qs.count(),
        "oldest_pending_hours": 0,
        "avg_queue_age_hours": 0,
        "approved_today": 0,
        "rejected_today": 0,
        "overdue_returns": 0,
        "per_house": [],
    }

    # --- Oldest + average age (in hours) for the SLA gauges ---
    # PostgreSQL has no AVG(timestamptz); averaging in Python keeps this
    # compatible with PostgreSQL 14 and avoids a fragile database-specific
    # epoch conversion. Pending queues are small by design.
    pending_timestamps = list(
        scoped_pending_qs.order_by().values_list("created_at", flat=True)
    )
    if pending_timestamps:
        oldest = min(pending_timestamps)
        stats["oldest_pending_hours"] = round(
            (now - oldest).total_seconds() / 3600, 1
        )
        total_age_seconds = sum(
            (now - created_at).total_seconds() for created_at in pending_timestamps
        )
        stats["avg_queue_age_hours"] = round(
            total_age_seconds / len(pending_timestamps) / 3600, 1
        )

    # --- Today's throughput for the current approver (SLA context) ---
    # Use the immutable decision timestamp and the named decision-maker, so a
    # Senior Housemaster without a HouseMaster profile still gets desk stats.
    today_by_me = (
        Exeat.objects.filter(
            decided_at__gte=today_start,
        ).filter(
            _Q(approved_by_user=user) | _Q(approved_by__user=user)
        )
        if user.is_authenticated
        else Exeat.objects.none()
    )
    stats["approved_today"] = today_by_me.filter(status=Exeat.Status.APPROVED).count()
    stats["rejected_today"] = today_by_me.filter(status=Exeat.Status.REJECTED).count()

    # --- Overdue currently-out (cross queue context for approvers) ---
    stats["overdue_returns"] = (
        Exeat.objects.filter(
            status=Exeat.Status.APPROVED,
            expected_return__lte=now,
        )
        .filter(
            _Q(
                movement__actual_departure__isnull=False,
                movement__actual_return__isnull=True,
            )
            | _Q(
                reconciliation__outcome=ExeatReconciliation.Outcome.STUDENT_OUT,
                reconciliation__actual_return__isnull=True,
            )
        )
        .filter(
            # For regular Housemasters scope overdue to their own house, not global
            student__house_id__in=(
                list(
                    set(
                        e.student.house_id
                        for e in scoped_pending_qs.select_related("student__house")
                        if e.student and e.student.house_id
                    )
                )
                if not (
                    user.is_superuser
                    or (
                        getattr(get_user_profile(user), "role", None)
                        in (StaffRole.ADMIN, StaffRole.SENIOR_HOUSEMASTER)
                    )
                )
                else _Q(pk__isnull=False)
            )  # admin/senior: unrestricted
        )
        .count()
    )

    # --- Per-house breakdown (for the queue-jump chip bar) ---
    house_totals = list(
        scoped_pending_qs.values(
            house_id=F("student__house_id"),
            house_name=F("student__house__name"),
            house_order=F("student__house__display_order"),
        )
        .annotate(pending=Count("id"))
        .order_by("house_order", "house_name")
    )
    stats["per_house"] = [
        {
            "id": row["house_id"],
            "name": row["house_name"] or "Unassigned",
            "pending": row["pending"],
        }
        for row in house_totals
        if row["house_id"] is not None
    ]
    # Also carry an "All houses" chip with total count
    if stats["per_house"]:
        stats["per_house"].insert(
            0, {"id": None, "name": "All Queues", "pending": stats["total_pending"]}
        )

    return stats
