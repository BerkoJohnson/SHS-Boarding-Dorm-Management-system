"""Forms for the public marketing website.

Server-side validation is authoritative. The browser-side attributes in the
templates are a convenience only; every rule enforced here is also enforced
when the form is submitted without JavaScript.
"""
from __future__ import annotations

import re

from django import forms

from website.models import DemoRequest, GhanaRegion, PreferredDemoMethod


class DemoRequestForm(forms.ModelForm):
    """Validated demonstration request submitted from the public site."""

    # Honeypot. Real visitors never see or fill this field. A non-empty value
    # is rejected so automated submissions are not silently stored.
    website = forms.CharField(
        required=False,
        label="",
        widget=forms.TextInput(
            attrs={
                "class": "honeypot",
                "autocomplete": "off",
                "tabindex": "-1",
                "aria-hidden": "true",
            }
        ),
    )

    # Explicit consent, so the basis for contact is recorded with the enquiry.
    consent = forms.BooleanField(
        required=True,
        label="I agree to Eden Creative Systems contacting me about this request.",
        widget=forms.CheckboxInput(attrs={"class": "mt-0.5 h-4 w-4 shrink-0"}),
    )

    class Meta:
        model = DemoRequest
        fields = (
            "school_name",
            "contact_name",
            "role",
            "phone",
            "email",
            "region",
            "boarding_population",
            "preferred_demo_method",
            "message",
        )
        widgets = {
            "school_name": forms.TextInput(
                attrs={
                    "class": "field-input",
                    "autocomplete": "organization",
                    "maxlength": 200,
                }
            ),
            "contact_name": forms.TextInput(
                attrs={
                    "class": "field-input",
                    "autocomplete": "name",
                    "maxlength": 150,
                }
            ),
            "role": forms.TextInput(
                attrs={
                    "class": "field-input",
                    "autocomplete": "organization-title",
                    "maxlength": 120,
                    "placeholder": "Headmaster, Assistant Headmaster, Matron",
                }
            ),
            "phone": forms.TextInput(
                attrs={
                    "class": "field-input",
                    "autocomplete": "tel",
                    "inputmode": "tel",
                    "maxlength": 32,
                    "placeholder": "024 000 0000",
                }
            ),
            "email": forms.EmailInput(
                attrs={
                    "class": "field-input",
                    "autocomplete": "email",
                    "maxlength": 254,
                }
            ),
            "boarding_population": forms.NumberInput(
                attrs={
                    "class": "field-input",
                    "min": 1,
                    "max": 20000,
                    "inputmode": "numeric",
                }
            ),
            "message": forms.Textarea(
                attrs={"class": "field-input", "rows": 5, "maxlength": 2000}
            ),
        }
        labels = {
            "school_name": "School name",
            "contact_name": "Your name",
            "role": "Your official role",
            "phone": "Phone number",
            "email": "Email address",
            "region": "Region",
            "boarding_population": "Estimated boarding students (optional)",
            "preferred_demo_method": "Preferred demo method (optional)",
            "message": "Message or special requirements (optional)",
        }
        help_texts = {
            "boarding_population": (
                "An approximation is fine. It only helps us prepare the right "
                "demonstration."
            ),
            "message": (
                "Tell us anything that matters about your boarding setup, such "
                "as the number of houses or how exeats are currently approved."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["region"].choices = [
            ("", "Select a region"),
            *GhanaRegion.choices,
        ]
        self.fields["region"].widget = forms.Select(
            choices=self.fields["region"].choices,
            attrs={"class": "field-input"},
        )

        self.fields["preferred_demo_method"].required = False
        self.fields["preferred_demo_method"].choices = [
            ("", "No preference"),
            *PreferredDemoMethod.choices,
        ]
        self.fields["preferred_demo_method"].widget = forms.Select(
            choices=self.fields["preferred_demo_method"].choices,
            attrs={"class": "field-input"},
        )

        # The honeypot must not appear in the field order the template renders.
        self.order_fields(
            [
                "school_name",
                "contact_name",
                "role",
                "phone",
                "email",
                "region",
                "boarding_population",
                "preferred_demo_method",
                "message",
                "consent",
                "website",
            ]
        )

        self._wire_accessibility_attributes()

    def _wire_accessibility_attributes(self) -> None:
        """Connect each control to its help and error text.

        Screen readers announce the error message and the guidance as part of
        the field, so a validation failure is understandable without sight.
        """
        for name, field in self.fields.items():
            attrs = field.widget.attrs
            described: list[str] = []
            if field.help_text:
                described.append(f"id_{name}-help")
                attrs.setdefault("aria-describedby", " ".join(described))
            if self.is_bound and name in self.errors:
                attrs["aria-invalid"] = "true"
                described.append(f"id_{name}-error")
                attrs["aria-describedby"] = " ".join(described)
                existing = attrs.get("class", "")
                if "field-invalid" not in existing:
                    attrs["class"] = f"{existing} field-invalid".strip()

    # -- validation ---------------------------------------------------------

    def clean(self):
        """Reject automated submissions without naming the honeypot field.

        Raised as a non-field error so the message is visible in the form's
        error summary. A real visitor is never asked to fill this field, so the
        message also points them at the phone and email fallbacks.
        """
        cleaned = super().clean()
        if (self.data.get("website") or "").strip():
            raise forms.ValidationError(
                "This request could not be submitted automatically. Please try "
                "again, or contact us by phone or email."
            )
        return cleaned

    def clean_school_name(self):
        value = (self.cleaned_data.get("school_name") or "").strip()
        if len(value) < 3:
            raise forms.ValidationError("Enter the full name of your school.")
        return value

    def clean_contact_name(self):
        value = (self.cleaned_data.get("contact_name") or "").strip()
        if len(value) < 2:
            raise forms.ValidationError("Enter your full name.")
        return value

    def clean_role(self):
        value = (self.cleaned_data.get("role") or "").strip()
        if len(value) < 2:
            raise forms.ValidationError("Enter your official role at the school.")
        return value

    def clean_phone(self) -> str:
        """Normalise a Ghanaian number to ``+233XXXXXXXXX`` where possible.

        Accepts the shapes people actually write: ``024 000 0000``,
        ``0240000000``, ``024-000-0000``, ``+233 24 000 0000``,
        ``233240000000`` and ``00233240000000``. A number that begins with the
        Ghana country code but has the wrong number of digits is rejected
        rather than passed through, so an obviously truncated number cannot be
        stored and later fail to receive a message.
        """
        raw = (self.cleaned_data.get("phone") or "").strip()
        compact = re.sub(r"[\s\-.()/]", "", raw)
        if not compact:
            raise forms.ValidationError(
                "Enter a phone number we can reach you on."
            )

        has_plus = compact.startswith("+")
        digits = compact.lstrip("+")
        if digits.startswith("00"):
            digits = digits[2:]
            has_plus = True

        if digits.startswith("233"):
            national = digits[3:]
            if len(national) == 9:
                return f"+233{national}"
            raise forms.ValidationError(
                "That Ghanaian number is not the right length. Enter it as "
                "024 000 0000 or +233 24 000 0000."
            )

        if len(digits) == 10 and digits.startswith("0"):
            return f"+233{digits[1:]}"

        if len(digits) == 9:
            return f"+233{digits}"

        if has_plus and re.fullmatch(r"\+\d{7,15}", compact):
            return compact

        raise forms.ValidationError(
            "Enter a valid phone number, for example 024 000 0000 or "
            "+233 24 000 0000."
        )

    def clean_email(self) -> str:
        return (self.cleaned_data.get("email") or "").strip().lower()

    def clean_boarding_population(self):
        value = self.cleaned_data.get("boarding_population")
        if value is None:
            return None
        if value < 1:
            raise forms.ValidationError(
                "Enter an estimate of at least 1, or leave this blank."
            )
        if value > 20000:
            raise forms.ValidationError(
                "That number looks larger than a boarding population. Leave it "
                "blank if you are unsure."
            )
        return value

    def clean_preferred_demo_method(self) -> str:
        value = self.cleaned_data.get("preferred_demo_method") or ""
        return value or PreferredDemoMethod.NO_PREFERENCE

    def clean_message(self) -> str:
        value = (self.cleaned_data.get("message") or "").strip()
        if len(value) > 2000:
            raise forms.ValidationError(
                "Please keep your message under 2,000 characters."
            )
        return value
