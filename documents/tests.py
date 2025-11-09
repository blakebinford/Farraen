import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from documents.models import Document
from organizations.models import Membership, Organization


class DocumentDetailViewTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media_root = tempfile.mkdtemp()
        cls._override = override_settings(MEDIA_ROOT=cls._media_root)
        cls._override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._override.disable()
        shutil.rmtree(cls._media_root, ignore_errors=True)
        super().tearDownClass()

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email="welder@example.com", password="password123"
        )
        cls.org = Organization.objects.create(name="Pipe Ledger", owner=cls.user)
        Membership.objects.create(
            org=cls.org, user=cls.user, role=Membership.Role.MEMBER
        )

    def setUp(self):
        self.client.force_login(self.user)

    def _create_document(self, *, doc_type="MTR", number="DOC-001"):
        file_content = SimpleUploadedFile("test.pdf", b"%PDF-1.4 test file")
        return Document.objects.create_document(
            org=self.org,
            doc_type=doc_type,
            number=number,
            title="Test Document",
            file=file_content,
            uploaded_by=self.user,
        )

    def test_mtr_detail_includes_material_heat_form(self):
        doc = self._create_document(doc_type="MTR", number="HEAT-1001")
        url = reverse("doc_detail", kwargs={"org_slug": self.org.slug, "doc_id": doc.id})

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertIn("material_heat_form", response.context)
        self.assertContains(response, "Material heat details")
        self.assertContains(response, "name=\"heat_number\"")
        self.assertContains(response, "Save heat details")

    def test_non_mtr_detail_does_not_include_material_heat_form(self):
        doc = self._create_document(doc_type="NDE", number="DRW-2001")
        url = reverse("doc_detail", kwargs={"org_slug": self.org.slug, "doc_id": doc.id})

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context.get("material_heat_form"))
        self.assertNotContains(response, "Material heat details")
        self.assertNotContains(response, "name=\"heat_number\"")
