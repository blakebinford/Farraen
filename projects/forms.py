from django import forms
from django.contrib.auth import get_user_model

from .models import Project, Tag


User = get_user_model()


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = [
            "name",
            "description",
            "project_code",
            "planned_start_date",
            "planned_end_date",
            "client_name",
            "site_address",
            "site_latitude",
            "site_longitude",
            "external_id",
            "project_manager",
            "superintendent",
            "quality_manager",
            "quality_techs",
            "tags",
            "status",
        ]
        widgets = {
            "planned_start_date": forms.DateInput(attrs={"type": "date"}),
            "planned_end_date": forms.DateInput(attrs={"type": "date"}),
            "quality_techs": forms.SelectMultiple(attrs={"class": "form-select"}),
            "tags": forms.SelectMultiple(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, org=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.org = org
        self.request_user = user
        user_qs = User.objects.all().order_by("email")
        self.fields["project_manager"].queryset = user_qs
        self.fields["superintendent"].queryset = user_qs
        self.fields["quality_manager"].queryset = user_qs
        self.fields["quality_techs"].queryset = user_qs
        self.fields["tags"].queryset = Tag.objects.all().order_by("name")
        self.fields["status"].initial = Project.Status.PLANNED
        for name, field in self.fields.items():
            widget = field.widget
            css_class = "form-control"
            if isinstance(widget, (forms.Select, forms.SelectMultiple)):
                css_class = "form-select"
            if isinstance(widget, forms.DateInput):
                css_class = "form-control"
            existing = widget.attrs.get("class", "")
            widget.attrs["class"] = f"{existing} {css_class}".strip()

    def clean_project_manager(self):
        value = self.cleaned_data.get("project_manager")
        if not value and self.request_user:
            return self.request_user
        return value

    def clean_status(self):
        status = self.cleaned_data["status"]
        if status == Project.Status.ARCHIVED:
            raise forms.ValidationError("New projects cannot start in the archived state.")
        return status


class ProjectInfoForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = [
            "name",
            "project_code",
            "client_name",
            "site_address",
            "planned_start_date",
            "planned_end_date",
            "description",
        ]
        widgets = {
            "planned_start_date": forms.DateInput(attrs={"type": "date"}),
            "planned_end_date": forms.DateInput(attrs={"type": "date"}),
            "description": forms.Textarea(attrs={"rows": 4}),
        }
        labels = {
            "site_address": "Location",
            "planned_start_date": "Start date",
            "planned_end_date": "End date",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            base_class = "form-control"
            if isinstance(widget, forms.Select):
                base_class = "form-select"
            existing = widget.attrs.get("class", "")
            widget.attrs["class"] = f"{existing} {base_class}".strip()


class ProjectStatusForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ["status"]
        widgets = {
            "status": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["status"].label = "Project status"
