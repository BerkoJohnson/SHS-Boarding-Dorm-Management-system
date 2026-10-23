"""Factories shared by the API test suite."""
from __future__ import annotations

import json

from django.contrib.auth import get_user_model

from core.models import (
    Dormitory,
    Guardian,
    House,
    HouseMaster,
    Programme,
    SchoolConfiguration,
    StaffRole,
    Student,
    UserProfile,
)

User = get_user_model()
PASSWORD = "StrongPassw0rd!2026"


def create_school(name="Test Senior High School", short_name="TSHS"):
    return SchoolConfiguration.objects.create(
        name=name,
        short_name=short_name,
        address="Test Town",
        contact_number="+233200000000",
    )


def create_staff(
    username,
    *,
    role=StaffRole.ADMIN,
    password=PASSWORD,
    house=None,
    senior=False,
    is_active=True,
    first_name="",
    last_name="",
):
    user = User.objects.create_user(
        username=username,
        password=password,
        first_name=first_name,
        last_name=last_name,
        is_active=is_active,
    )
    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.role = role
    profile.save(update_fields=["role"])
    if house is not None or senior:
        HouseMaster.objects.create(
            user=user,
            full_name=f"{first_name} {last_name}".strip() or username,
            staff_id=f"STF-{username}"[:30],
            phone_number="+233200000001",
            house=house,
            is_senior_housemaster=senior,
        )
    return user


def create_house(name="Test House"):
    return House.objects.create(name=name)


def create_guardian(full_name="Test Guardian", phone_number="+233200000222"):
    return Guardian.objects.create(full_name=full_name, phone_number=phone_number)


def create_student(
    admission_no,
    *,
    full_name=None,
    index_number=None,
    house=None,
    programme=None,
    dormitory=None,
    room=None,
    class_group=None,
    guardian=None,
    residency=Student.ResidencyStatus.BOARDING,
    status=Student.EnrollmentStatus.ENROLLED,
    gender=Student.Gender.MALE,
    photo=None,
):
    if programme is None:
        programme, _ = Programme.objects.get_or_create(name="General Arts")
    if (
        residency == Student.ResidencyStatus.BOARDING
        and dormitory is None
        and house is not None
    ):
        # Boarding students must have a dormitory block. Tests that do not care
        # about placement get a house-local default rather than repeating it.
        dormitory, _ = Dormitory.objects.get_or_create(
            house=house,
            name=f"Default {gender.title()} Block",
            defaults={
                "gender": (
                    Dormitory.GenderBlock.MALE
                    if gender == Student.Gender.MALE
                    else Dormitory.GenderBlock.FEMALE
                ),
                "capacity": 100,
            },
        )
    if residency == Student.ResidencyStatus.BOARDING and dormitory is None:
        residency = Student.ResidencyStatus.DAY
    return Student.objects.create(
        admission_no=admission_no,
        index_number=index_number or f"IDX-{admission_no}",
        full_name=full_name or f"Student {admission_no}",
        gender=gender,
        programme=programme,
        house=house,
        dormitory=dormitory,
        room=room,
        class_group=class_group,
        guardian=guardian,
        residency=residency,
        status=status,
        photo=photo or "",
    )


def login(client, username, password=PASSWORD, **extra):
    payload = {"username": username, "password": password}
    payload.update(extra)
    return client.post(
        "/api/v1/auth/login/",
        data=json.dumps(payload),
        content_type="application/json",
    )


def auth(token):
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def token_from(response):
    return response.json()["data"]["token"]
