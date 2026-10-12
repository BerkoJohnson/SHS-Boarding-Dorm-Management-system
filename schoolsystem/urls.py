from pathlib import Path

from django.contrib import admin
from django.conf import settings
from django.urls import path, include
from django.views.static import serve

def serve_school_logo(request, path):
    return serve(
        request,
        path,
        document_root=Path(settings.MEDIA_ROOT) / "school" / "logos",
    )


urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('core.urls')),
]

if settings.MEDIA_STORAGE_BACKEND == "filesystem":
    urlpatterns += [
        path(
            f"{settings.MEDIA_URL.lstrip('/')}school/logos/<path:path>",
            serve_school_logo,
        ),
    ]
