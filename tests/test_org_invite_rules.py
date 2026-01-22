import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from organizations.models import Invitation, Membership, Organization


@pytest.mark.django_db
def test_org_admin_cannot_invite_admin(client):
    user_model = get_user_model()
    owner = user_model.objects.create_user(username="owner", password="pw")
    admin = user_model.objects.create_user(username="admin", password="pw")

    org = Organization.objects.create(name="Org", slug="org", owner=owner)
    Membership.objects.create(org=org, user=owner, role=Membership.Role.OWNER)
    Membership.objects.create(org=org, user=admin, role=Membership.Role.ADMIN)

    client.force_login(admin)
    response = client.post(
        reverse("invite_member", kwargs={"org_slug": org.slug}),
        data={"email": "new-admin@example.com", "role": Membership.Role.ADMIN},
    )

    assert response.status_code == 403
    assert Invitation.objects.filter(org=org).count() == 0


@pytest.mark.django_db
def test_org_owner_can_invite_admin(client):
    user_model = get_user_model()
    owner = user_model.objects.create_user(username="owner", password="pw")

    org = Organization.objects.create(name="Org", slug="org", owner=owner)
    Membership.objects.create(org=org, user=owner, role=Membership.Role.OWNER)

    client.force_login(owner)
    response = client.post(
        reverse("invite_member", kwargs={"org_slug": org.slug}),
        data={"email": "new-admin@example.com", "role": Membership.Role.ADMIN},
    )

    assert response.status_code == 302
    invite = Invitation.objects.get(org=org, email="new-admin@example.com")
    assert invite.role == Membership.Role.ADMIN
