from django.conf import settings


class ContentSecurityPolicyReportOnlyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        return self.process_response(request, response)

    def process_response(self, request, response):
        response.setdefault(
            "Content-Security-Policy-Report-Only",
            settings.CSP_REPORT_ONLY_POLICY,
        )
        return response
