import core.storage
from django.core.files.base import File
from django.core.validators import FileExtensionValidator
from django.core.files.storage import storages
from django.db import migrations, models


def move_student_photos_to_private_storage(apps, schema_editor):
    Student = apps.get_model("core", "Student")
    public_storage = storages["default"]
    private_storage = core.storage.PrivateStudentPhotoStorage()

    for photo_name in Student.objects.exclude(photo="").values_list(
        "photo", flat=True
    ):
        if private_storage.exists(photo_name):
            if public_storage.exists(photo_name):
                public_storage.delete(photo_name)
            continue
        if not public_storage.exists(photo_name):
            raise FileNotFoundError(
                f"Student photo {photo_name!r} is missing from the configured "
                "default storage; refusing to complete the privacy migration."
            )

        with public_storage.open(photo_name, "rb") as source:
            saved_name = private_storage.save(photo_name, File(source))
        if saved_name != photo_name:
            private_storage.delete(saved_name)
            raise RuntimeError(
                f"Student photo {photo_name!r} could not be moved without "
                "changing its stored name."
            )
        public_storage.delete(photo_name)


def restore_student_photos_to_default_storage(apps, schema_editor):
    Student = apps.get_model("core", "Student")
    public_storage = storages["default"]
    private_storage = core.storage.PrivateStudentPhotoStorage()

    for photo_name in Student.objects.exclude(photo="").values_list(
        "photo", flat=True
    ):
        if not private_storage.exists(photo_name):
            if public_storage.exists(photo_name):
                continue
            raise FileNotFoundError(
                f"Student photo {photo_name!r} is missing from private storage."
            )
        if public_storage.exists(photo_name):
            raise FileExistsError(
                f"Cannot restore student photo {photo_name!r}: a public file "
                "already exists with that name."
            )

        with private_storage.open(photo_name, "rb") as source:
            saved_name = public_storage.save(photo_name, File(source))
        if saved_name != photo_name:
            public_storage.delete(saved_name)
            raise RuntimeError(
                f"Student photo {photo_name!r} could not be restored without "
                "changing its stored name."
            )
        private_storage.delete(photo_name)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0023_schoolconfiguration_short_name"),
    ]

    operations = [
        migrations.RunPython(
            move_student_photos_to_private_storage,
            restore_student_photos_to_default_storage,
        ),
        migrations.AlterField(
            model_name="student",
            name="photo",
            field=models.ImageField(
                blank=True,
                help_text=(
                    "Optional portrait photo. JPEG or PNG, maximum 5 MB and "
                    "25 megapixels."
                ),
                storage=core.storage.PrivateStudentPhotoStorage(),
                upload_to="students/photos/%Y/%m/",
                validators=[
                    FileExtensionValidator(allowed_extensions=["jpg", "jpeg", "png"]),
                    core.models.validate_student_photo,
                ],
            ),
        ),
        migrations.AlterField(
            model_name="userprofile",
            name="role",
            field=models.CharField(
                choices=[
                    ("UNASSIGNED", "Unassigned"),
                    ("ADMIN", "System Administrator"),
                    ("SENIOR_HOUSEMASTER", "Senior Housemaster"),
                    ("HOUSEMASTER", "Housemaster / Housemistress"),
                    ("HOUSE_TUTOR", "House Tutor"),
                    ("GATE_OFFICER", "Security / Gate Officer"),
                ],
                default="UNASSIGNED",
                help_text="Institutional operational role governing permissions.",
                max_length=30,
            ),
        ),
    ]
