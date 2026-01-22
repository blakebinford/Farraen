import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from documents.models import Document
from organizations.models import Membership, Organization


class DocumentUploadViewTests(TestCase):
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
            email="uploader@example.com", password="password123"
        )
        cls.org = Organization.objects.create(name="Pipe Ledger", owner=cls.user)
        Membership.objects.create(
            org=cls.org, user=cls.user, role=Membership.Role.MEMBER
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_upload_form_includes_name_field(self):
        url = reverse("doc_upload", kwargs={"org_slug": self.org.slug})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="name"')
        self.assertContains(response, 'name="doc_type"')
        self.assertContains(response, 'name="number"')

    def test_successful_upload_redirects_to_list_for_non_mtr(self):
        url = reverse("doc_upload", kwargs={"org_slug": self.org.slug})
        file_content = SimpleUploadedFile("drawing.pdf", b"PDF data")
        response = self.client.post(
            url,
            data={
                "name": "drawing.pdf",
                "doc_type": Document.DocType.NDE,
                "number": "DRAW-100",
                "title": "",
                "file": file_content,
            },
        )

        self.assertRedirects(
            response,
            reverse("doc_list", kwargs={"org_slug": self.org.slug}),
        )

        doc = Document.objects.get(number="DRAW-100")
        self.assertEqual(doc.name, "drawing.pdf")
        self.assertEqual(doc.title, "drawing.pdf")

    def test_successful_upload_redirects_to_detail_for_mtr(self):
        url = reverse("doc_upload", kwargs={"org_slug": self.org.slug})
        file_content = SimpleUploadedFile("mtr.pdf", b"PDF data")
        response = self.client.post(
            url,
            data={
                "name": "mtr.pdf",
                "doc_type": Document.DocType.MTR,
                "number": "MTR-100",
                "title": "",
                "file": file_content,
            },
        )

        doc = Document.objects.get(number="MTR-100")

        self.assertRedirects(
            response,
            reverse(
                "doc_detail",
                kwargs={"org_slug": self.org.slug, "doc_id": doc.id},
            ),
        )


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
        cls.guest = get_user_model().objects.create_user(
            email="guest@example.com", password="password123"
        )
        cls.org = Organization.objects.create(name="Pipe Ledger", owner=cls.user)
        Membership.objects.create(
            org=cls.org, user=cls.user, role=Membership.Role.MEMBER
        )
        Membership.objects.create(
            org=cls.org, user=cls.guest, role=Membership.Role.GUEST
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

    def test_kpi_template_blocked_for_guests(self):
        doc = self._create_document(doc_type="NDE", number="KPI-001")
        doc.is_kpi_template = True
        doc.save(update_fields=["is_kpi_template"])
        url = reverse("doc_detail", kwargs={"org_slug": self.org.slug, "doc_id": doc.id})

        self.client.force_login(self.guest)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

        self.client.force_login(self.user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
