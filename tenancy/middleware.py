from django.conf import settings
from django.core.exceptions import DisallowedHost, ValidationError
from django.http import Http404
from django.urls import set_urlconf
from django.utils.deprecation import MiddlewareMixin
from django_pgschemas.routing.info import DomainInfo
from django_pgschemas.schema import activate, activate_public

from .models import Domain, normalize_domain


class VerifiedDomainRoutingMiddleware(MiddlewareMixin):
    def process_request(self, request):
        activate_public()
        set_urlconf(None)
        try:
            host = normalize_domain(request.get_host().split(":", 1)[0])
        except (DisallowedHost, ValidationError) as error:
            raise Http404("No active school is configured for this hostname.") from error

        domain = (
            Domain.objects.select_related("tenant")
            .filter(
                domain=host,
                folder="",
                is_verified=True,
                tenant__is_active=True,
            )
            .first()
        )
        if domain is None:
            raise Http404("No active school is configured for this hostname.")

        tenant = domain.tenant
        tenant.routing = DomainInfo(domain=host)
        request.tenant = tenant
        request.urlconf = settings.TENANTS["default"]["URLCONF"]
        set_urlconf(request.urlconf)
        activate(tenant)
        return None

    def process_response(self, request, response):
        activate_public()
        set_urlconf(None)
        return response
