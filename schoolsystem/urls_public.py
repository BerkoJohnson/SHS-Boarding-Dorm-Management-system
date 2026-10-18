"""URLconf for the public Eden Creative Systems marketing website.

This URLconf is selected by ``tenancy.middleware.VerifiedDomainRoutingMiddleware``,
which sets ``request.urlconf`` to ``settings.PUBLIC_WEBSITE_URLCONF`` (this
module) for any host listed in ``settings.PUBLIC_WEBSITE_DOMAIN``, after
activating the public schema.

It is deliberately *not* declared as ``TENANTS["public"]["URLCONF"]``:
``django-pgschemas`` rejects a ``URLCONF`` key on the public schema (see
``django_pgschemas.checks.ensure_public_schema``), so the public host is
dispatched with a project setting instead.

Only the public website is routed. The authenticated school application
(``core``) and the Django admin are deliberately absent, so this URLconf
cannot expose school operations, student records, or tenant data.
"""
from django.urls import include, path

from website import views

urlpatterns = [
    path("robots.txt", views.robots_txt, name="public_robots_txt"),
    path("sitemap.xml", views.sitemap_xml, name="public_sitemap_xml"),
    path("", include("website.urls")),
]

handler403 = "website.views.public_permission_denied_view"
handler404 = "website.views.public_page_not_found_view"
handler500 = "website.views.public_server_error_view"
