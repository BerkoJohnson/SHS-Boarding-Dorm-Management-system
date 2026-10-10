from django.conf import settings
from django.core.files.storage import FileSystemStorage


class PrivateStudentPhotoStorage(FileSystemStorage):
    def __init__(self, location=None, base_url=None, **kwargs):
        super().__init__(
            location=location,
            base_url=None,
            **kwargs,
        )

    @property
    def location(self):
        return str(
            self._location
            if self._location is not None
            else settings.PRIVATE_STUDENT_PHOTO_ROOT
        )

    def url(self, name):
        raise ValueError(
            "Student photos must be served through an authorized photo view."
        )
