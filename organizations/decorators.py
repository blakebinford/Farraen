# organizations/decorators.py
from functools import wraps
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseForbidden
from django.core.exceptions import PermissionDenied

from .models import Membership

# Allowed role order for threshold checks
ROLE_ORDER = ["GUEST", "VIEWER", "MEMBER", "ADMIN", "OWNER"]

def require_membership(min_role="VIEWER", allow_guest_readonly=True):
    """
    Ensure the current user is a member of request.org and (optionally) enforces a minimum role.
    Usage:
        @require_membership()                       # default VIEWER+
        @require_membership(min_role="MEMBER")      # MEMBER+
        @require_membership(min_role="ADMIN")       # ADMIN+
    Notes:
        - Guests are allowed but (by default) only GET is permitted for them.
        - Requires CurrentOrganizationMiddleware to have set request.org.
    """
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def _wrapped(request, *args, **kwargs):
            org = getattr(request, "org", None)
            if org is None:
                raise Http404("Organization not found")

            try:
                m = Membership.objects.get(org=org, user=request.user)
            except Membership.DoesNotExist:
                # Not a member of this org at all
                raise PermissionDenied("You do not have access to this organization.")

            # Guests: by default block non-GET actions
            if (
                m.role == Membership.Role.GUEST
                and allow_guest_readonly
                and request.method != "GET"
            ):
                return HttpResponseForbidden("Guest users have read-only access.")

            # Minimum role threshold (GUEST < VIEWER < MEMBER < ADMIN < OWNER)
            if ROLE_ORDER.index(m.role) < ROLE_ORDER.index(min_role):
                raise PermissionDenied("Insufficient role.")

            return view_func(request, *args, **kwargs)

        return _wrapped
    return decorator
