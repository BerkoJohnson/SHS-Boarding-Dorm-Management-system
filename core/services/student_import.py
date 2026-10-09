import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zipfile import BadZipFile

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from core.models import (
    ClassGroup,
    Dormitory,
    Guardian,
    House,
    Programme,
    Student,
)


MAX_ROSTER_ROWS = 2000
REQUIRED_COLUMNS = {
    "admission_no",
    "index_number",
    "full_name",
    "gender",
    "programme",
}
COLUMN_ALIASES = {
    "admissionno": "admission_no",
    "admissionnumber": "admission_no",
    "indexno": "index_number",
    "indexnumber": "index_number",
    "fullname": "full_name",
    "studentname": "full_name",
    "name": "full_name",
    "gender": "gender",
    "dateofbirth": "date_of_birth",
    "dob": "date_of_birth",
    "programme": "programme",
    "program": "programme",
    "class": "class_group",
    "classgroup": "class_group",
    "form": "class_group",
    "residency": "residency",
    "residencystatus": "residency",
    "house": "house",
    "boardinghouse": "house",
    "dormitory": "dormitory",
    "dorm": "dormitory",
    "block": "dormitory",
    "status": "status",
    "enrollmentstatus": "status",
    "guardianname": "guardian_name",
    "parentname": "guardian_name",
    "guardianphone": "guardian_phone",
    "guardiancontact": "guardian_phone",
    "parentphone": "guardian_phone",
    "parentcontact": "guardian_phone",
    "guardianaltphone": "guardian_alt_phone",
    "parentaltcontact": "guardian_alt_phone",
}


@dataclass
class StagedStudent:
    student: Student
    guardian_name: str
    guardian_phone: str
    guardian_alt_phone: str


def _text(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _normalized_header(value):
    return re.sub(r"[^a-z0-9]", "", _text(value).lower())


def _row_values(headers, values, sheet_name, row_number, errors):
    values = list(values)
    if not any(_text(value) for value in values):
        return None
    if len(values) > len(headers) and any(
        _text(value) for value in values[len(headers):]
    ):
        errors.append(
            f"{sheet_name}, row {row_number}: data exists beyond the last header."
        )
    mapped = {}
    for index, raw_header in enumerate(headers):
        canonical = COLUMN_ALIASES.get(_normalized_header(raw_header))
        if not canonical:
            continue
        if canonical in mapped:
            errors.append(
                f"{sheet_name}, row {row_number}: column '{raw_header}' duplicates "
                f"the {canonical.replace('_', ' ')} column."
            )
            continue
        mapped[canonical] = _text(values[index] if index < len(values) else "")
    return mapped


def _read_csv(data):
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("CSV files must use UTF-8 encoding.") from error
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        headers = next(reader)
    except StopIteration:
        raise ValueError("The uploaded CSV file is empty.") from None
    return [("CSV", headers, row_number, row) for row_number, row in enumerate(reader, 2)]


def _read_xlsx(data):
    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except (InvalidFileException, BadZipFile, OSError, ValueError) as error:
        raise ValueError("The uploaded file is not a readable Excel .xlsx workbook.") from error

    rows = []
    try:
        for sheet in workbook.worksheets:
            iterator = sheet.iter_rows(values_only=True)
            headers = next(iterator, None)
            if not headers or not any(_text(value) for value in headers):
                continue
            for row_number, row in enumerate(iterator, 2):
                rows.append((sheet.title, headers, row_number, row))
                if len(rows) > MAX_ROSTER_ROWS + len(workbook.worksheets):
                    raise ValueError(
                        f"Roster files cannot contain more than {MAX_ROSTER_ROWS} student rows."
                    )
    finally:
        workbook.close()
    if not rows:
        raise ValueError("The Excel workbook has no student rows.")
    return rows


def _reference_index(objects, attribute):
    index = {}
    for obj in objects:
        key = _text(getattr(obj, attribute)).casefold()
        index.setdefault(key, []).append(obj)
    return index


def _reference(value, index, field, location, errors):
    if not value:
        return None
    matches = index.get(value.casefold(), [])
    if len(matches) == 1:
        return matches[0]
    if not matches:
        errors.append(f"{location}: unknown {field} '{value}'.")
    else:
        errors.append(f"{location}: {field} '{value}' is ambiguous.")
    return None


def _parse_date(value, location, errors):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _text(value)
    for date_format in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, date_format).date()
        except ValueError:
            continue
    errors.append(
        f"{location}: date of birth must be YYYY-MM-DD or DD/MM/YYYY."
    )
    return None


def _choice_value(value, choices, field, location, errors, default=""):
    if not value:
        return default
    normalized = value.casefold()
    for choice_value, label in choices:
        if normalized in {str(choice_value).casefold(), str(label).casefold()}:
            return choice_value
    errors.append(f"{location}: invalid {field} '{value}'.")
    return default


def import_student_roster(uploaded_file):
    """Validate a complete CSV/XLSX roster, then create it atomically."""
    extension = Path(uploaded_file.name).suffix.lower()
    if extension not in {".csv", ".xlsx"}:
        return 0, ["Upload a CSV or Excel .xlsx file."]
    if uploaded_file.size > 10 * 1024 * 1024:
        return 0, ["Roster files must be 10 MB or smaller."]

    data = uploaded_file.read()
    try:
        raw_rows = (
            _read_csv(data) if extension == ".csv" else _read_xlsx(data)
        )
    except ValueError as error:
        return 0, [str(error)]

    errors = []
    parsed_rows = []
    data_row_count = 0
    checked_sheets = set()
    for sheet_name, headers, row_number, row in raw_rows:
        if sheet_name not in checked_sheets:
            checked_sheets.add(sheet_name)
            available = {
                COLUMN_ALIASES.get(_normalized_header(header))
                for header in headers
            }
            missing = REQUIRED_COLUMNS - available
            if missing:
                errors.append(
                    f"{sheet_name}: missing required columns: "
                    f"{', '.join(sorted(name.replace('_', ' ') for name in missing))}."
                )
                continue
        mapped = _row_values(headers, row, sheet_name, row_number, errors)
        if mapped is not None:
            data_row_count += 1
            parsed_rows.append((sheet_name, row_number, mapped))
    if data_row_count > MAX_ROSTER_ROWS:
        errors.append(
            f"Roster files cannot contain more than {MAX_ROSTER_ROWS} student rows."
        )
    if not parsed_rows and not errors:
        errors.append("The roster file contains no student rows.")
    if errors:
        return 0, errors

    programmes = list(Programme.objects.all())
    programme_index = _reference_index(programmes, "name")
    for programme in programmes:
        if programme.code:
            code_matches = programme_index.setdefault(
                programme.code.casefold(), []
            )
            if all(match.pk != programme.pk for match in code_matches):
                code_matches.append(programme)
    class_index = _reference_index(ClassGroup.objects.all(), "name")
    house_index = _reference_index(House.objects.all(), "name")
    dormitories = list(Dormitory.objects.select_related("house").all())
    dormitory_index = {}
    for dormitory in dormitories:
        dormitory_index.setdefault(
            (dormitory.house_id, dormitory.name.casefold()), []
        ).append(dormitory)
    boarding_dormitories = {}
    for dormitory in dormitories:
        boarding_dormitories.setdefault(
            (dormitory.house_id, dormitory.gender), []
        ).append(dormitory)

    existing_admission_numbers = {
        value.casefold()
        for value in Student.objects.values_list("admission_no", flat=True)
    }
    existing_index_numbers = {
        value.casefold()
        for value in Student.objects.values_list("index_number", flat=True)
    }
    seen_admission_numbers = set()
    seen_index_numbers = set()
    guardians_by_phone = {}
    for guardian in Guardian.objects.all():
        guardians_by_phone.setdefault(guardian.phone_number, []).append(guardian)

    staged = []
    for sheet_name, row_number, row in parsed_rows:
        location = f"{sheet_name}, row {row_number}"
        admission_no = row.get("admission_no", "").strip()
        index_number = row.get("index_number", "").strip()
        for value, seen, existing, label in (
            (
                admission_no,
                seen_admission_numbers,
                existing_admission_numbers,
                "admission number",
            ),
            (
                index_number,
                seen_index_numbers,
                existing_index_numbers,
                "index number",
            ),
        ):
            normalized_value = value.casefold()
            if value and (
                normalized_value in seen or normalized_value in existing
            ):
                errors.append(f"{location}: duplicate {label} '{value}'.")
            seen.add(normalized_value)

        gender = _choice_value(
            row.get("gender", ""),
            Student.Gender.choices,
            "gender",
            location,
            errors,
        )
        residency = _choice_value(
            row.get("residency", ""),
            Student.ResidencyStatus.choices,
            "residency",
            location,
            errors,
            Student.ResidencyStatus.BOARDING,
        )
        status = _choice_value(
            row.get("status", ""),
            Student.EnrollmentStatus.choices,
            "enrollment status",
            location,
            errors,
            Student.EnrollmentStatus.ENROLLED,
        )
        programme_value = row.get("programme", "").strip()
        programme = _reference(
            programme_value, programme_index, "programme", location, errors
        )
        class_value = row.get("class_group", "").strip()
        class_group = _reference(
            class_value, class_index, "class", location, errors
        ) if class_value else None
        house_value = row.get("house", "").strip()
        house = _reference(
            house_value, house_index, "house", location, errors
        ) if house_value else None
        dormitory_value = row.get("dormitory", "").strip()
        dormitory = None
        if dormitory_value:
            possible_dormitories = dormitory_index.get(
                (house.pk, dormitory_value.casefold()), []
            ) if house else []
            if len(possible_dormitories) == 1:
                dormitory = possible_dormitories[0]
            elif not house:
                errors.append(
                    f"{location}: select a house before specifying a dormitory."
                )
            elif not possible_dormitories:
                errors.append(
                    f"{location}: unknown dormitory '{dormitory_value}' in {house.name}."
                )
            else:
                errors.append(
                    f"{location}: dormitory '{dormitory_value}' is ambiguous in {house.name}."
                )
        elif residency == Student.ResidencyStatus.BOARDING and house:
            possible_dormitories = boarding_dormitories.get(
                (
                    house.pk,
                    Dormitory.GenderBlock.MALE
                    if gender == Student.Gender.MALE
                    else Dormitory.GenderBlock.FEMALE,
                ),
                [],
            )
            if len(possible_dormitories) == 1:
                dormitory = possible_dormitories[0]
            elif len(possible_dormitories) > 1:
                errors.append(
                    f"{location}: specify a dormitory; {house.name} has multiple "
                    "blocks for this student's gender."
                )

        dob = _parse_date(row.get("date_of_birth"), location, errors)
        guardian_name = row.get("guardian_name", "").strip()
        guardian_phone = row.get("guardian_phone", "").strip()
        guardian_alt_phone = row.get("guardian_alt_phone", "").strip()
        if bool(guardian_name) != bool(guardian_phone):
            errors.append(
                f"{location}: guardian name and primary phone must be provided together."
            )
        if guardian_alt_phone and not guardian_phone:
            errors.append(
                f"{location}: provide the guardian primary phone before an alternative phone."
            )
        guardian = None
        if guardian_phone:
            try:
                candidate = Guardian(
                    full_name=guardian_name,
                    phone_number=guardian_phone,
                    alt_phone_number=guardian_alt_phone or None,
                )
                candidate.full_clean()
            except ValidationError as error:
                errors.extend(
                    f"{location}: guardian {field}: {message}"
                    for field, messages in error.message_dict.items()
                    for message in messages
                )
            existing_guardians = guardians_by_phone.get(guardian_phone, [])
            if existing_guardians:
                matching_guardians = [
                    item for item in existing_guardians
                    if item.full_name.casefold() == guardian_name.casefold()
                ]
                if not matching_guardians:
                    errors.append(
                        f"{location}: guardian phone is already linked to another name."
                    )
                else:
                    guardian = matching_guardians[0]
            else:
                for staged_row in staged:
                    if (
                        staged_row.guardian_phone == guardian_phone
                        and staged_row.guardian_name.casefold()
                        != guardian_name.casefold()
                    ):
                        errors.append(
                            f"{location}: guardian phone is used with different names in the file."
                        )
                        break

        student = Student(
            admission_no=admission_no,
            index_number=index_number,
            full_name=row.get("full_name", "").strip(),
            gender=gender,
            date_of_birth=dob,
            programme=programme,
            house=house,
            dormitory=dormitory,
            class_group=class_group,
            guardian=guardian,
            residency=residency,
            status=status,
        )
        try:
            student.full_clean()
        except ValidationError as error:
            errors.extend(
                f"{location}: {field}: {message}"
                for field, messages in error.message_dict.items()
                for message in messages
            )
        staged.append(
            StagedStudent(
                student=student,
                guardian_name=guardian_name,
                guardian_phone=guardian_phone,
                guardian_alt_phone=guardian_alt_phone,
            )
        )

    if errors:
        return 0, errors

    try:
        with transaction.atomic():
            created_guardians = {}
            for item in staged:
                if item.guardian_phone:
                    guardian = next(
                        (
                            guardian
                            for guardian in guardians_by_phone.get(
                                item.guardian_phone, []
                            )
                            if guardian.full_name.casefold()
                            == item.guardian_name.casefold()
                        ),
                        None,
                    )
                    if guardian is None:
                        guardian = created_guardians.get(item.guardian_phone)
                    if guardian is None:
                        guardian = Guardian.objects.create(
                            full_name=item.guardian_name,
                            phone_number=item.guardian_phone,
                            alt_phone_number=item.guardian_alt_phone or None,
                        )
                        created_guardians[item.guardian_phone] = guardian
                    item.student.guardian = guardian
                item.student.save()
    except IntegrityError:
        return 0, [
            "The roster could not be imported because a student identifier was "
            "added or changed during validation. No students were imported; "
            "please upload the file again."
        ]
    return len(staged), []
