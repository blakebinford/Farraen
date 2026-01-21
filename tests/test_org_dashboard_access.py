import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from organizations.models import Membership, Organization


@pytest.mark.django_db
def test_org_dashboard_requires_membership(client):
    user_model = get_user_model()
    member = user_model.objects.create_user(username="member", password="pw")
    outsider = user_model.objects.create_user(username="outsider", password="pw")
    org = Organization.objects.create(name="Farraen QA", slug="farraen-qa", owner=member)
    Membership.objects.create(org=org, user=member, role=Membership.Role.GUEST)

    client.force_login(outsider)
    response = client.get(reverse("org_dashboard", kwargs={"org_slug": org.slug}))
    assert response.status_code == 403

    client.force_login(member)
    response = client.get(reverse("org_dashboard", kwargs={"org_slug": org.slug}))
    assert response.status_code == 200
