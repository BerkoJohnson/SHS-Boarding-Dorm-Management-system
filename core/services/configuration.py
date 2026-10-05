"""
Service layer for system configuration.
"""
from django.db import transaction
from core.models import AcademicTerm

def set_active_term(term_id) -> AcademicTerm:
    """
    Sets the specified AcademicTerm as active and deactivates others.
    """
    with transaction.atomic():
        term = AcademicTerm.objects.select_for_update().get(id=term_id)
        
        # Deactivate other terms with the same level, or just all terms to be safe
        # depending on business logic. The system uses LevelChoice.ALL predominantly.
        AcademicTerm.objects.filter(level=term.level).exclude(id=term_id).update(is_current=False)
        
        term.is_current = True
        term.save()
        
    return term
