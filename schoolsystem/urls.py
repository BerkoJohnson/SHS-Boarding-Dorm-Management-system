from django.contrib import admin
from django.conf import settings
from django.urls import path, include
from django.views.static import serve

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('core.urls')),
]

if settings.DEBUG:
    urlpatterns += [
        path(
            f"{settings.MEDIA_URL.lstrip('/')}school/logos/<path:path>",
            serve,
            {"document_root": settings.MEDIA_ROOT / "school" / "logos"},
        ),
    ]
