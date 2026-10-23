from pathlib import Path

from django.contrib import admin
from django.conf import settings
from django.http import Http404
from django.urls import path, include
from django.views.static import serve

def serve_school_logo(request, path):
    return serve(
        request,
        path,
        document_root=Path(settings.MEDIA_ROOT) / "school" / "logos",
    )


def serve_tenant_school_logo(request, schema, path):
    tenant = getattr(request, "tenant", None)
    if tenant is None or tenant.schema_name != schema:
        raise Http404
    return serve(
        request,
        f"tenants/{schema}/school/logos/{path}",
        document_root=settings.MEDIA_ROOT,
    )


urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/v1/', include('core.api.urls')),
    path('', include('core.urls')),
]

handler403 = "core.views.permission_denied_view"

if settings.MEDIA_STORAGE_BACKEND == "filesystem":
    urlpatterns += [
        path(
            f"{settings.MEDIA_URL.lstrip('/')}school/logos/<path:path>",
            serve_school_logo,
        ),
        path(
            f"{settings.MEDIA_URL.lstrip('/')}tenants/<slug:schema>/school/logos/<path:path>",
            serve_tenant_school_logo,
        ),
    ]


# ---------------------------------------------------------------------------
# Public marketing website (local preview only)
# ---------------------------------------------------------------------------
# In multi-tenant deployments the public website is served from its own host by
# VerifiedDomainRoutingMiddleware, which points ``request.urlconf`` at
# ``settings.PUBLIC_WEBSITE_URLCONF`` (see ``schoolsystem/urls_public.py``).
# When tenancy is disabled there is no host-based routing, so the website is
# mounted under an explicit prefix for local development and review only. It is
# never mounted at the site root, because the root URL belongs to the
# authenticated school dashboard.
if not settings.TENANCY_ENABLED:
    urlpatterns += [
        path("site/", include("website.urls")),
    ]
