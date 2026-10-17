from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from storages.backends.s3 import S3Storage


class PrivateUrlMixin:
    """Forbid public URLs for private tenant media.

    Private files (student photos, generated student-ID exports) are only ever
    served through an authorized application view, so asking a private storage
    backend for a URL is always a programming error.
    """

    def url(self, name):
        raise ValueError(
            "Private files must be served through an authorized view."
        )


class PrivateS3Storage(PrivateUrlMixin, S3Storage):
    """Shared private object storage for the documented web/worker topology.

    Objects are uploaded without a public ACL and are read back through the
    application, so a Celery worker and the web process see the same files.
    """


def build_private_s3_storage(location):
    """Build an S3 backend for private media using the configured bucket.

    Reuses the credentials/bucket selected by ``MEDIA_STORAGE_BACKEND=neon_s3``
    for ``STORAGES['default']`` but writes under a private ``location`` prefix
    so private objects stay separate from public media.
    """
    default_options = dict(
        settings.STORAGES.get("default", {}).get("OPTIONS", {})
    )
    default_options["location"] = location
    return PrivateS3Storage(**default_options)


class _S3DelegatingMixin:
    """Route private media to S3 when the shared S3 backend is configured.

    In filesystem mode the storage behaves exactly like a local
    ``FileSystemStorage``. When ``MEDIA_STORAGE_BACKEND=neon_s3`` the same
    storage instance transparently delegates file operations to a private S3
    prefix so photos and export archives survive restarts/redeploys and are
    readable from every service.
    """

    s3_location = ""

    def uses_s3(self):
        return (
            getattr(settings, "MEDIA_STORAGE_BACKEND", "filesystem") == "neon_s3"
        )

    def _remote_storage(self):
        backend = getattr(self, "_remote_backend", None)
        if backend is None:
            backend = build_private_s3_storage(self.s3_location)
            self._remote_backend = backend
        return backend

    def _open(self, name, mode="rb"):
        if self.uses_s3():
            return self._remote_storage()._open(name, mode)
        return super()._open(name, mode)

    def _save(self, name, content):
        if self.uses_s3():
            return self._remote_storage()._save(name, content)
        return super()._save(name, content)

    def exists(self, name):
        if self.uses_s3():
            return self._remote_storage().exists(name)
        return super().exists(name)

    def delete(self, name):
        if self.uses_s3():
            return self._remote_storage().delete(name)
        return super().delete(name)

    def path(self, name):
        if self.uses_s3():
            raise NotImplementedError(
                "This storage backend does not expose absolute paths."
            )
        return super().path(name)

    def size(self, name):
        if self.uses_s3():
            return self._remote_storage().size(name)
        return super().size(name)

    def listdir(self, path):
        if self.uses_s3():
            return self._remote_storage().listdir(path)
        return super().listdir(path)


class PrivateFileSystemStorage(PrivateUrlMixin, _S3DelegatingMixin, FileSystemStorage):
    """Filesystem private storage whose root is resolved from settings."""

    def __init__(self, location=None, base_url=None, **kwargs):
        super().__init__(
            location=location,
            base_url=None,
            **kwargs,
        )

    def _filesystem_location(self):
        raise NotImplementedError

    @property
    def location(self):
        if self._location is not None:
            return str(self._location)
        return str(self._filesystem_location())


class PrivateStudentPhotoStorage(PrivateFileSystemStorage):
    s3_location = "private/student_photos"

    def _filesystem_location(self):
        return settings.PRIVATE_STUDENT_PHOTO_ROOT


class PrivateStudentIDExportStorage(PrivateFileSystemStorage):
    s3_location = "private/student_id_exports"

    def _filesystem_location(self):
        return Path(settings.BASE_DIR) / ".private" / "student_id_exports"
