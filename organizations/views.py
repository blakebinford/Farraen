from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponseBadRequest
from .models import Organization, Membership, Invitation
from .decorators import require_membership

@login_required
@require_membership("GUEST")
def org_dashboard(request, org_slug):
    org = request.org
    if not org:
        return HttpResponseBadRequest("Organization not found")
    return render(request, "organizations/dashboard.html", {"org": org})

@login_required
@require_membership("ADMIN")
def org_members(request, org_slug):
    org = request.org
    memberships = Membership.objects.filter(org=org).select_related("user")
    invites = Invitation.objects.filter(org=org, accepted_at__isnull=True)
    return render(request, "organizations/members.html", {"org": org, "memberships": memberships, "invites": invites})

@login_required
@require_membership("ADMIN")
def invite_member(request, org_slug):
    org = request.org
    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()
        role = request.POST.get("role", "MEMBER")
        if not email:
            return HttpResponseBadRequest("Email required")
        inv = Invitation.objects.create(org=org, email=email, role=role, invited_by=request.user)
        # TODO: send email with link
        # Link: /o/<org_slug>/invite/accept/<token>/
        return redirect("org_members", org_slug=org.slug)
    return render(request, "organizations/invite.html", {"org": org})

@login_required
def accept_invite(request, org_slug, token):
    org = request.org
    inv = get_object_or_404(Invitation, org=org, token=token)
    inv.accept(request.user)
    return redirect("org_dashboard", org_slug=org.slug)
