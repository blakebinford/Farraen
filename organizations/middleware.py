from .models import Organization
from django.utils.deprecation import MiddlewareMixin

class CurrentOrganizationMiddleware(MiddlewareMixin):
    """
    URL pattern: /o/<org_slug>/...
    Looks for 'org_slug' in the resolved URL and sets request.org
    """
    def process_view(self, request, view_func, view_args, view_kwargs):
        org_slug = None
        if request.resolver_match:
            org_slug = request.resolver_match.kwargs.get("org_slug")
        request.org = None
        if org_slug:
            try:
                request.org = Organization.objects.get(slug=org_slug)
            except Organization.DoesNotExist:
                request.org = None
        return None
