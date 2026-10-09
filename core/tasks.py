from __future__ import annotations

import logging
from datetime import timedelta
from zipfile import ZIP_DEFLATED, ZipFile

from celery import shared_task
from django.conf import settings
from django.db.models import F, Q
from django.urls import reverse
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=4)
def send_sms_delivery_task(self, delivery_id: str) -> bool:
    from core.models import SMSConfiguration, SMSDelivery
    from core.services.sms import send_sms

    delivery = (
        SMSDelivery.objects.filter(pk=delivery_id)
        .select_related("exeat", "exeat__student")
        .first()
    )
    if delivery is None:
        logger.error("SMS delivery %s does not exist", delivery_id)
        return False
    if delivery.status == SMSDelivery.Status.SENT:
        return True
    if not SMSConfiguration.is_enabled():
        logger.warning("SMS delivery %s remains queued because SMS is disabled", delivery_id)
        return False
    if delivery.status != SMSDelivery.Status.QUEUED:
        return False

    attempted_at = timezone.now()
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

    if send_sms(delivery.phone_number, delivery.message):
        delivery.status = SMSDelivery.Status.SENT
        delivery.sent_at = timezone.now()
        delivery.last_error = ""
        delivery.save(
            update_fields=["status", "sent_at", "last_error", "updated_at"]
        )
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
    delivery.save(
        update_fields=["status", "last_error", "updated_at"]
    )
    logger.error(
        "SMS delivery %s failed after %s attempts",
        delivery_id,
        delivery.attempts,
    )
    return False


@shared_task
def dispatch_queued_sms_deliveries() -> int:
    from core.models import SMSConfiguration, SMSDelivery
    from core.services.sms import enqueue_sms_delivery_task

    if not SMSConfiguration.is_enabled():
        return 0

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
        enqueue_sms_delivery_task(delivery_id)
    return len(queued_ids)


@shared_task
def enqueue_overdue_exeat_reminders() -> int:
    from core.models import Exeat, ExeatReconciliation, SMSConfiguration, SMSDelivery
    from core.services.sms import enqueue_exeat_event_sms

    if not SMSConfiguration.is_enabled():
        return 0

    now = timezone.now()
    active_exeats = (
        Exeat.objects.filter(status=Exeat.Status.APPROVED)
        .filter(
            Q(movement__actual_departure__isnull=False, movement__actual_return__isnull=True)
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

        reminder_number = int(
            (now - exeat.expected_return).total_seconds() // timedelta(days=1).total_seconds()
        )
        existing_key_prefix = (
            f"{SMSDelivery.EventType.OVERDUE}:{exeat.pk}:"
        )
        if SMSDelivery.objects.filter(
            exeat=exeat,
            event_type=SMSDelivery.EventType.OVERDUE,
            dedupe_key__startswith=existing_key_prefix,
            dedupe_key__endswith=f":{reminder_number}",
        ).exists():
            continue

        deliveries = enqueue_exeat_event_sms(
            exeat,
            SMSDelivery.EventType.OVERDUE,
            occurrence_key=str(reminder_number),
        )
        count += len(deliveries)
    return count


@shared_task
def generate_student_id_export(export_id: str, public_origin: str) -> bool:
    from core.models import SchoolConfiguration, Student, StudentIDExport
    from core.services.student_id_images import (
        private_student_id_export_path,
        render_student_card_side,
        safe_student_folder_name,
    )

    export = StudentIDExport.objects.filter(pk=export_id).first()
    if export is None:
        logger.error("Student ID export %s does not exist", export_id)
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
    total_students = students.count()
    if not total_students:
        export.status = StudentIDExport.Status.FAILED
        export.error_message = "There are no enrolled students to export."
        export.finished_at = timezone.now()
        export.save(
            update_fields=["status", "error_message", "finished_at", "updated_at"]
        )
        return False

    configuration = SchoolConfiguration.get_solo()
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

    archive_path = private_student_id_export_path(export.archive_name)
    temporary_path = archive_path.with_suffix(".tmp")
    extension = "png" if export.image_format == StudentIDExport.ImageFormat.PNG else "jpg"
    try:
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(temporary_path, "w", compression=ZIP_DEFLATED) as archive:
            for processed, student in enumerate(students.iterator(chunk_size=100), start=1):
                folder_name = safe_student_folder_name(student)
                public_url = (
                    f"{public_origin.rstrip('/')}"
                    f"{reverse('core:public_student_exeat', args=[student.public_token])}"
                )
                for side in ("front", "back"):
                    image_bytes = render_student_card_side(
                        student=student,
                        school_configuration=configuration,
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

        temporary_path.replace(archive_path)
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
        temporary_path.unlink(missing_ok=True)
        archive_path.unlink(missing_ok=True)
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
