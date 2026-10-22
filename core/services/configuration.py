"""
Service layer for system configuration.
"""
from django.db import transaction
from core.models import AcademicTerm, AcademicYear


def lock_academic_term_updates():
    """Serialize term activation, including when no term is currently active."""
    list(
        AcademicYear.objects.select_for_update()
        .order_by("pk")
        .values_list("pk", flat=True)
    )


def set_active_term(term_id) -> AcademicTerm:
    """
    Sets the specified AcademicTerm as active and deactivates others.
    """
    with transaction.atomic():
        lock_academic_term_updates()
        term = AcademicTerm.objects.select_for_update().get(id=term_id)
        AcademicTerm.objects.filter(
            level=term.level,
            is_current=True,
        ).exclude(id=term_id).update(is_current=False)
        term.is_current = True
        term.save()
    return term
