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
