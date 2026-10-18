"""URLs for the public Eden Creative Systems marketing website.

Included by ``schoolsystem/urls_public.py`` (multi-tenant public host) and, for
local preview only, at ``/site/`` by ``schoolsystem/urls.py`` when tenancy is
disabled.
"""
from django.urls import path

from website import views

app_name = "website"

urlpatterns = [
    path("", views.home, name="home"),
    path("features/", views.features, name="features"),
    path("services/", views.services, name="services"),
    path("how-it-works/", views.how_it_works, name="how_it_works"),
    path("pricing/", views.pricing_page, name="pricing"),
    path("faqs/", views.faq, name="faq"),
    path("contact/", views.contact, name="contact"),
    path("request-a-demo/", views.demo_request, name="demo_request"),
    path("privacy-policy/", views.privacy, name="privacy"),
    path("terms-of-service/", views.terms, name="terms"),
]
