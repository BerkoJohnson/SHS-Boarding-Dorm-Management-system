from __future__ import annotations

import logging
import tempfile
import uuid
from contextlib import nullcontext
from datetime import timedelta
from functools import wraps
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from celery import shared_task
from django.conf import settings
from django.core.files import File
from django.db import transaction
from django.db.models import F, Q
from django.urls import reverse
from django.utils import timezone

logger = logging.getLogger(__name__)


def _as_uuid(value):
    """Return ``value`` as a UUID instance, or ``None`` when it is not valid.

    Task arguments arrive from the broker as plain strings, so a value that is
    not a well-formed UUID would otherwise raise a ``ValidationError`` deep in
    the queryset and fail the task instead of being reported clearly.
    """
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (AttributeError, TypeError, ValueError):
        return None


def _tenant_context(tenant_schema):
    if not settings.TENANCY_ENABLED:
        return nullcontext()
    if not tenant_schema:
        raise ValueError("Tenant-scoped tasks require a tenant schema name.")
    from tenancy.models import Tenant

    tenant = Tenant.objects.filter(
        schema_name=tenant_schema,
        is_active=True,
    ).first()
    if tenant is None:
        raise ValueError(f"Active tenant schema '{tenant_schema}' was not found.")
    return tenant


def _with_tenant_schema(task_function):
    @wraps(task_function)
    def wrapped(*args, tenant_schema=None, **kwargs):
        with _tenant_context(tenant_schema):
            return task_function(*args, tenant_schema=tenant_schema, **kwargs)

    return wrapped


def _run_for_each_tenant(task, tenant_schema, operation):
    if not settings.TENANCY_ENABLED:
        return operation()
    if tenant_schema:
        with _tenant_context(tenant_schema):
            return operation()

    from tenancy.models import Tenant

    schemas = list(
        Tenant.objects.filter(is_active=True).values_list("schema_name", flat=True)
    )
    for schema_name in schemas:
        task.apply_async(kwargs={"tenant_schema": schema_name})
    return len(schemas)


@shared_task(bind=True, max_retries=4)
@_with_tenant_schema
def send_sms_delivery_task(
    self,
    delivery_id: str,
    tenant_schema: str | None = None,
) -> bool:
    from core.models import SMSConfiguration, SMSDelivery
    from core.services.sms import send_sms

    delivery_pk = _as_uuid(delivery_id)
    if delivery_pk is None:
        logger.error("SMS delivery %r has an invalid identifier", delivery_id)
        return False
    delivery = (
        SMSDelivery.objects.filter(pk=delivery_pk)
        .select_related("exeat", "exeat__student")
        .first()
    )
    if delivery is None:
        logger.error("SMS delivery %s does not exist", delivery_id)
        return False
    if delivery.status == SMSDelivery.Status.SENT:
        return True
    if not SMSConfiguration.is_enabled():
        logger.warning(
            "SMS delivery %s remains queued because SMS is disabled", delivery_id
        )
        return False
    if delivery.status != SMSDelivery.Status.QUEUED:
        return False
    from tenancy.entitlements import quota_guard, sms_quota_allows

    attempted_at = timezone.now()
    with transaction.atomic():
        # Serialize the monthly-SMS check with the status transition that
        # consumes the delivery's budget slot.
        with quota_guard("max_sms_monthly", schema_name=tenant_schema):
            if not sms_quota_allows(
                0,
                exclude_delivery_id=delivery.pk,
                schema_name=tenant_schema,
            ):
                logger.warning(
                    "SMS delivery %s remains queued because the monthly SMS limit was reached",
                    delivery_id,
                )
                return False
            claimed = SMSDelivery.objects.filter(
                pk=delivery.pk,
                status=SMSDelivery.Status.QUEUED,
            ).update(
                status=SMSDelivery.Status.PROCESSING,
                attempts=F("attempts") + 1,
                last_attempted_at=attempted_at,
                updated_at=attempted_at,
            )
    if not claimed:
        return False
    delivery.status = SMSDelivery.Status.PROCESSING
    delivery.attempts += 1
    delivery.last_attempted_at = attempted_at

    if send_sms(
        delivery.phone_number,
        delivery.message,
        delivery_id=str(delivery.pk),
    ):
        delivery.status = SMSDelivery.Status.SENT
        delivery.sent_at = timezone.now()
        delivery.last_error = ""
        delivery.save(update_fields=["status", "sent_at", "last_error", "updated_at"])
        return True

    max_attempts = max(1, int(getattr(settings, "SMS_MAX_ATTEMPTS", 5)))
    error_message = "SMSOnlineGH did not confirm successful delivery"
    delivery.last_error = error_message
    retries_allowed = max_attempts - 1
    if self.request.retries < retries_allowed:
        delay = min(2 ** (self.request.retries + 1), 300)
        delivery.status = SMSDelivery.Status.QUEUED
        delivery.scheduled_for = timezone.now() + timedelta(seconds=delay)
        delivery.save(
            update_fields=["status", "last_error", "scheduled_for", "updated_at"]
        )
        raise self.retry(
            exc=RuntimeError(error_message),
            countdown=delay,
            max_retries=retries_allowed,
        )

    delivery.status = SMSDelivery.Status.FAILED
    delivery.save(update_fields=["status", "last_error", "updated_at"])
    logger.error(
        "SMS delivery %s failed after %s attempts",
        delivery_id,
        delivery.attempts,
    )
    return False


@shared_task
def dispatch_queued_sms_deliveries(tenant_schema: str | None = None) -> int:
    return _run_for_each_tenant(
        dispatch_queued_sms_deliveries,
        tenant_schema,
        _dispatch_queued_sms_deliveries_for_current_tenant,
    )


def _dispatch_queued_sms_deliveries_for_current_tenant() -> int:
    from core.models import SMSConfiguration, SMSDelivery
    from core.services.sms import enqueue_sms_delivery_task

    if not SMSConfiguration.is_enabled():
        return 0

    tenant_schema = None
    if settings.TENANCY_ENABLED:
        from django_pgschemas.schema import get_current_schema

        schema = get_current_schema()
        tenant_schema = getattr(schema, "schema_name", None) if schema else None

    now = timezone.now()
    SMSDelivery.objects.filter(
        status=SMSDelivery.Status.PROCESSING,
        last_attempted_at__lt=now - timedelta(minutes=5),
    ).update(
        status=SMSDelivery.Status.QUEUED,
        scheduled_for=now,
        last_error="Recovered stale processing delivery",
        updated_at=now,
    )
    queued_ids = list(
        SMSDelivery.objects.filter(
            status=SMSDelivery.Status.QUEUED,
            scheduled_for__lte=now,
        )
        .order_by("created_at")
        .values_list("pk", flat=True)[:100]
    )
    for delivery_id in queued_ids:
        enqueue_sms_delivery_task(delivery_id, tenant_schema=tenant_schema)
    return len(queued_ids)


@shared_task
def enqueue_overdue_exeat_reminders(tenant_schema: str | None = None) -> int:
    return _run_for_each_tenant(
        enqueue_overdue_exeat_reminders,
        tenant_schema,
        _enqueue_overdue_exeat_reminders_for_current_tenant,
    )


def _enqueue_overdue_exeat_reminders_for_current_tenant() -> int:
    from core.models import Exeat, ExeatReconciliation, SMSConfiguration, SMSDelivery
    from core.services.late_returns import (
        notify_missed_return,
        open_late_return_incident,
    )
    from core.services.sms import enqueue_exeat_event_sms

    # Incidents and in-app escalation notifications are operational controls and
    # must run even while SMS is disabled (D6/F-07).  Only the SMS enqueue step
    # is gated by SMS configuration.
    sms_enabled = SMSConfiguration.is_enabled()
    now = timezone.now()
    active_exeats = (
        Exeat.objects.filter(status=Exeat.Status.APPROVED)
        .filter(
            Q(
                movement__actual_departure__isnull=False,
                movement__actual_return__isnull=True,
            )
            | Q(
                reconciliation__outcome=ExeatReconciliation.Outcome.STUDENT_OUT,
                reconciliation__actual_return__isnull=True,
            )
        )
        .select_related("student", "student__guardian", "student__house")
    )

    count = 0
    for exeat in active_exeats:
        if not exeat.expected_return or now < exeat.expected_return:
            continue

        # D3 first stage: alert the Housemaster as soon as the return is missed,
        # before the incident threshold opens a formal investigation.
        notify_missed_return(exeat, detected_at=now)

        # Create or escalate the written late-return incident.  This is
        # idempotent: repeated task runs only notify when the escalation level
        # changes.
        open_late_return_incident(exeat, detected_at=now, source="scheduled task")

        if not sms_enabled:
            continue

        reminder_number = int(
            (now - exeat.expected_return).total_seconds()
            // timedelta(days=1).total_seconds()
        )
        # Within the first 24 hours the first-stage warning is the guardian
        # message. Daily reminders begin on day one so the guardian is not sent
        # two duplicate transactional messages in the same run.
        occurrence_key = "first" if reminder_number == 0 else str(reminder_number)
        existing_key_prefix = f"{SMSDelivery.EventType.OVERDUE}:{exeat.pk}:"
        if SMSDelivery.objects.filter(
            exeat=exeat,
            event_type=SMSDelivery.EventType.OVERDUE,
            dedupe_key__startswith=existing_key_prefix,
            dedupe_key__endswith=f":{occurrence_key}",
        ).exists():
            continue

        deliveries = enqueue_exeat_event_sms(
            exeat,
            SMSDelivery.EventType.OVERDUE,
            occurrence_key=occurrence_key,
        )
        count += len(deliveries)
    return count


@shared_task
def expire_unfinished_roll_calls(tenant_schema: str | None = None) -> int:
    return _run_for_each_tenant(
        expire_unfinished_roll_calls,
        tenant_schema,
        _expire_unfinished_roll_calls_for_current_tenant,
    )


def _expire_unfinished_roll_calls_for_current_tenant() -> int:
    from core.models import RollCallNotification, RollCallSession
    from core.services.operational import get_operational_settings, roll_call_timeout

    now = timezone.now()
    timeout = roll_call_timeout()
    configured_minutes = get_operational_settings().roll_call_timeout_minutes
    expired_ids = RollCallSession.objects.filter(
        status=RollCallSession.Status.IN_PROGRESS,
        created_at__lte=now - timeout,
    ).values_list("pk", flat=True)[:100]

    expired_count = 0
    for session_id in expired_ids:
        with transaction.atomic():
            session = (
                RollCallSession.objects.select_for_update(of=("self",))
                .select_related("created_by", "conducted_by__user", "house")
                .filter(
                    pk=session_id,
                    status=RollCallSession.Status.IN_PROGRESS,
                    created_at__lte=now - timeout,
                )
                .first()
            )
            if session is None:
                continue

            owner = session.created_by
            if owner is None and session.conducted_by_id:
                owner = session.conducted_by.user
            if owner is not None:
                RollCallNotification.objects.create(
                    user=owner,
                    title="Unfinished roll call expired",
                    message=(
                        f"The {session.get_session_type_display()} for "
                        f"{session.house.name} expired because it was not "
                        f"completed within {configured_minutes} minutes. Its "
                        "attendance records were preserved for review."
                    ),
                )
            else:
                logger.warning(
                    "Expired roll call %s has no creator to notify",
                    session.pk,
                )
            session.status = RollCallSession.Status.EXPIRED
            note = (
                "Expired automatically after the configured "
                f"{configured_minutes}-minute completion window; attendance "
                "records retained for review."
            )
            session.notes = (
                f"{session.notes.rstrip()}\n{note}"
                if session.notes
                else note
            )
            session.save(update_fields=["status", "notes", "updated_at"])
            expired_count += 1
    return expired_count


@shared_task
def phase_out_shs3_students(tenant_schema: str | None = None) -> int:
    return _run_for_each_tenant(
        phase_out_shs3_students,
        tenant_schema,
        _phase_out_shs3_students_for_current_year,
    )


def _phase_out_shs3_students_for_current_year() -> int:
    from core.models import AcademicYear, Student, StudentProgressionRecord

    today = timezone.localdate()
    year_id = (
        AcademicYear.objects.filter(
            end_date__lte=today,
            students_phased_out_at__isnull=True,
        )
        .values_list("pk", flat=True)
        .first()
    )
    if year_id is None:
        return 0

    with transaction.atomic():
        year = AcademicYear.objects.select_for_update().get(pk=year_id)
        if year.end_date > today or year.students_phased_out_at is not None:
            return 0

        students = list(
            Student.objects.select_for_update()
            .filter(
                status=Student.EnrollmentStatus.ENROLLED,
                class_group__name__istartswith="3",
                expected_completion_year=year,
            )
            .select_related("class_group")
            .order_by("pk")
        )
        for student in students:
            StudentProgressionRecord.objects.create(
                student=student,
                academic_year=year,
                event=StudentProgressionRecord.Event.PHASED_OUT,
                from_class_group=student.class_group,
                effective_on=year.end_date,
            )
            student.status = Student.EnrollmentStatus.COMPLETED
            student.completed_on = year.end_date
            student.completion_academic_year = year
            student.save(
                update_fields=[
                    "status",
                    "completed_on",
                    "completion_academic_year",
                    "updated_at",
                ]
            )

        year.students_phased_out_at = timezone.now()
        year.save(update_fields=["students_phased_out_at", "updated_at"])
    return len(students)


@shared_task
def cleanup_expired_student_id_exports(tenant_schema: str | None = None) -> int:
    return _run_for_each_tenant(
        cleanup_expired_student_id_exports,
        tenant_schema,
        _cleanup_expired_student_id_exports_for_current_tenant,
    )


def _cleanup_expired_student_id_exports_for_current_tenant() -> int:
    from core.models import StudentIDExport
    from core.services.student_id_images import (
        student_id_export_name,
        student_id_export_storage,
    )

    storage = student_id_export_storage()
    expired_exports = StudentIDExport.objects.filter(
        expires_at__lte=timezone.now(),
        status__in=(
            StudentIDExport.Status.COMPLETED,
            StudentIDExport.Status.FAILED,
        ),
    )
    deleted_count = 0
    for export in expired_exports.iterator():
        name = student_id_export_name(export.archive_name)
        try:
            storage.delete(name)
        except OSError:
            logger.exception(
                "Could not delete expired student ID export archive %s",
                export.archive_name,
            )
            continue
        export.delete()
        deleted_count += 1
    return deleted_count


@shared_task
@_with_tenant_schema
def generate_student_id_export(
    export_id: str,
    public_origin: str,
    tenant_schema: str | None = None,
) -> bool:
    from core.models import SchoolConfiguration, Student, StudentIDExport
    from core.services.student_id_images import (
        render_student_card_side,
        safe_student_folder_name,
        student_id_export_name,
        student_id_export_storage,
    )

    export_pk = _as_uuid(export_id)
    if export_pk is None:
        logger.error("Student ID export %r has an invalid identifier", export_id)
        return False
    export = StudentIDExport.objects.filter(pk=export_pk).first()
    if export is None:
        logger.error("Student ID export %s does not exist", export_id)
        return False
    from tenancy.entitlements import feature_enabled

    if not feature_enabled("student_id_cards"):
        export.status = StudentIDExport.Status.FAILED
        export.error_message = (
            "This tenant's subscription does not include student ID cards."
        )
        export.finished_at = timezone.now()
        export.save(
            update_fields=["status", "error_message", "finished_at", "updated_at"]
        )
        return False
    if export.status != StudentIDExport.Status.QUEUED:
        logger.warning(
            "Student ID export %s was not queued (status: %s)",
            export_id,
            export.status,
        )
        return False

    students = (
        Student.objects.filter(status=Student.EnrollmentStatus.ENROLLED)
        .select_related("programme", "house", "class_group")
        .order_by("full_name", "admission_no")
    )
    school_configuration = SchoolConfiguration.get_solo()
    total_students = students.count()
    if not total_students:
        export.status = StudentIDExport.Status.FAILED
        export.error_message = "There are no enrolled students to export."
        export.finished_at = timezone.now()
        export.save(
            update_fields=["status", "error_message", "finished_at", "updated_at"]
        )
        return False

    export.status = StudentIDExport.Status.PROCESSING
    export.total_students = total_students
    export.processed_students = 0
    export.save(
        update_fields=[
            "status",
            "total_students",
            "processed_students",
            "updated_at",
        ]
    )

    storage = student_id_export_storage()
    archive_name = student_id_export_name(export.archive_name)
    extension = (
        "png" if export.image_format == StudentIDExport.ImageFormat.PNG else "jpg"
    )
    temporary_path = None
    try:
        # Build the archive on local scratch space, then upload the finished ZIP
        # to the shared private storage. This keeps the worker's temp files off
        # shared storage and means the web service can read the archive back
        # even when the two run on separate machines.
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as handle:
            temporary_path = Path(handle.name)
        with ZipFile(temporary_path, "w", compression=ZIP_DEFLATED) as archive:
            for processed, student in enumerate(
                students.iterator(chunk_size=100), start=1
            ):
                folder_name = safe_student_folder_name(student)
                public_url = (
                    f"{public_origin.rstrip('/')}"
                    f"{reverse('core:public_student_exeat', args=[student.public_token])}"
                )
                for side in ("front", "back"):
                    image_bytes = render_student_card_side(
                        student=student,
                        school_configuration=school_configuration,
                        public_url=public_url,
                        layout=export.layout,
                        side=side,
                        image_format=export.image_format,
                    )
                    archive.writestr(
                        f"{folder_name}/{side}.{extension}",
                        image_bytes,
                    )
                StudentIDExport.objects.filter(pk=export.pk).update(
                    processed_students=processed,
                    updated_at=timezone.now(),
                )

        with temporary_path.open("rb") as source:
            saved_name = storage.save(archive_name, File(source))
        if saved_name != archive_name:
            raise RuntimeError(
                f"Export archive was stored as {saved_name!r} instead of "
                f"{archive_name!r}."
            )

        export.status = StudentIDExport.Status.COMPLETED
        export.processed_students = total_students
        export.finished_at = timezone.now()
        export.error_message = ""
        export.save(
            update_fields=[
                "status",
                "processed_students",
                "finished_at",
                "error_message",
                "updated_at",
            ]
        )
        return True
    except Exception as error:
        try:
            storage.delete(archive_name)
        except Exception:  # noqa: BLE001 - never mask the original failure
            logger.exception(
                "Could not remove partial student ID export archive %s",
                archive_name,
            )
        export.status = StudentIDExport.Status.FAILED
        export.error_message = str(error)
        export.finished_at = timezone.now()
        export.save(
            update_fields=[
                "status",
                "error_message",
                "finished_at",
                "updated_at",
            ]
        )
        logger.exception("Student ID export %s failed", export_id)
        raise
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
