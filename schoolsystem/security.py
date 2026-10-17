import secrets

from django.conf import settings


class ContentSecurityPolicyReportOnlyMiddleware:
    """Emit a report-only CSP and a per-request nonce for future enforcement.

    A nonce is generated for every request and exposed on
    ``request.csp_nonce`` (readable in templates as ``{{ request.csp_nonce }}``)
    so inline ``<script>`` / ``<style>`` blocks can be allow-listed without
    ``'unsafe-inline'`` once the policy is switched from report-only to
    enforcing. Any ``{nonce}`` placeholder in ``CSP_REPORT_ONLY_POLICY`` is
    substituted with the request nonce.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.csp_nonce = secrets.token_urlsafe(16)
        response = self.get_response(request)
        return self.process_response(request, response)

    def process_response(self, request, response):
        policy = settings.CSP_REPORT_ONLY_POLICY
        if "{nonce}" in policy:
            policy = policy.replace("{nonce}", getattr(request, "csp_nonce", ""))
        response.setdefault("Content-Security-Policy-Report-Only", policy)
        return response
