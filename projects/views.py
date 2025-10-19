from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from organizations.decorators import require_membership
from .models import Project, ProjectMember

@login_required
@require_membership("GUEST")
def project_list(request, org_slug):
    qs = Project.objects.filter(org=request.org, is_archived=False)
    # Only show projects the user belongs to
    my = qs.filter(memberships__user=request.user).distinct()
    return render(request, "projects/list.html", {"org": request.org, "projects": my})

@login_required
@require_membership("MEMBER")
def project_create(request, org_slug):
    if request.method == "POST":
        name = request.POST.get("name","").strip()
        if not name:
            return render(request, "projects/create.html", {"org": request.org, "error": "Name required"})
        p = Project.objects.create(org=request.org, name=name, created_by=request.user)
        # add creator as manager
        ProjectMember.objects.create(project=p, user=request.user, role=ProjectMember.Role.MANAGER, added_by=request.user)
        return redirect("project_list", org_slug=request.org.slug)
    return render(request, "projects/create.html", {"org": request.org})
