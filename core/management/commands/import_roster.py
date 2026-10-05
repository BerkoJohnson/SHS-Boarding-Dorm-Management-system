from datetime import datetime
import pandas as pd
from django.core.management.base import BaseCommand
from core.models import Student, Programme, House, Dormitory, ClassGroup, Guardian


class Command(BaseCommand):
    help = "Import students roster from Excel file"

    def add_arguments(self, parser):
        parser.add_argument('file_path', type=str, help="Path to the roster Excel file")

    def handle(self, *args, **options):
        file_path = options['file_path']
        xls = pd.ExcelFile(file_path)

        num_map = {'ONE': 1, 'TWO': 2, 'THREE': 3, 'FOUR': 4}

        for sheet in xls.sheet_names:
            self.stdout.write(f"Processing sheet: {sheet}...")
            df = pd.read_excel(file_path, sheet_name=sheet)

            for _, row in df.iterrows():
                # 1. Programme
                prog_obj, _ = Programme.objects.get_or_create(
                    name=str(row['Programme']).strip().upper()
                )

                # 2. ClassGroup
                class_str = str(row['Class']).strip() if pd.notnull(row['Class']) else ''
                class_obj = None
                if class_str and class_str.lower() != 'unallocated':
                    class_obj, _ = ClassGroup.objects.get_or_create(
                        name=class_str,
                        defaults={'programme': prog_obj}
                    )

                # 3. House & Dormitory Processing
                house_str = str(row['House']).strip() if pd.notnull(row['House']) else ''
                house_obj = None
                dorm_obj = None

                if house_str and house_str.lower() != 'unallocated':
                    # Parse house number designation (1 to 4)
                    h_num = 1
                    for k, v in num_map.items():
                        if k in house_str.upper():
                            h_num = v
                            break

                    # Extract clean house name (e.g., "Red House", "Blue House")
                    clean_house_name = house_str.split('(')[0].strip().title()
                    if not clean_house_name.lower().endswith('house'):
                        clean_house_name = f"{clean_house_name} House"

                    # Get or create administrative House
                    house_obj, _ = House.objects.get_or_create(
                        number=h_num,
                        defaults={
                            'name': clean_house_name
                        }
                    )

                    # Determine gender block from string '(M)' or '(F)'
                    gender_code = Dormitory.GenderBlock.MALE if '(M)' in house_str.upper() else Dormitory.GenderBlock.FEMALE
                    block_label = "Boys Block" if gender_code == Dormitory.GenderBlock.MALE else "Girls Block"
                    dorm_name = f"{house_obj.name} {block_label}"

                    # Get or create the physical Dormitory block linked to this House
                    dorm_obj, _ = Dormitory.objects.get_or_create(
                        house=house_obj,
                        gender=gender_code,
                        name=dorm_name,
                        defaults={'capacity': 100}
                    )

                # 4. Residency Status
                residency_val = str(row['Residency']).strip().title() if pd.notnull(row['Residency']) else 'Boarding'

                # Explicitly clear dormitory for Day students to satisfy model clean() rules
                if residency_val == Student.ResidencyStatus.DAY:
                    dorm_obj = None

                # 5. Guardian Processing
                guardian_obj = None
                p_name = str(row['Parent Name']).strip() if pd.notnull(row['Parent Name']) else ''
                p_contact_raw = str(row['Parent Contact']).strip() if pd.notnull(row['Parent Contact']) else ''
                p_contact = p_contact_raw.split('.')[0] if p_contact_raw else ''

                if p_name and p_contact:
                    guardian_obj, _ = Guardian.objects.get_or_create(
                        phone_number=p_contact,
                        defaults={'full_name': p_name}
                    )

                # 6. Parse Date of Birth
                dob = None
                if pd.notnull(row['Date of Birth']):
                    dob_str = str(row['Date of Birth']).strip()
                    try:
                        dob = datetime.strptime(dob_str, '%d/%m/%Y').date()
                    except ValueError:
                        try:
                            dob = pd.to_datetime(dob_str).date()
                        except Exception:
                            pass

                # 7. Student Record Update or Creation
                student, _ = Student.objects.update_or_create(
                    admission_no=str(row['Admission No.']).strip(),
                    defaults={
                        'index_number': str(row['Index Number']).strip(),
                        'full_name': str(row['Full Name']).strip(),
                        'gender': str(row['Gender']).strip().lower(),
                        'date_of_birth': dob,
                        'programme': prog_obj,
                        'house': house_obj,
                        'dormitory': dorm_obj,
                        'class_group': class_obj,
                        'guardian': guardian_obj,
                        'residency': residency_val,
                        'status': str(row['Status']).strip().title() if pd.notnull(row['Status']) else 'Enrolled',
                    }
                )

        self.stdout.write(self.style.SUCCESS("Roster import completed successfully!"))