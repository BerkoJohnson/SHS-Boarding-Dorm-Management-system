from django.forms import ClearableFileInput


class PrivateStudentPhotoInput(ClearableFileInput):
    template_name = "core/widgets/private_clearable_file_input.html"

    def is_initial(self, value):
        return bool(value and getattr(value, "name", None))
