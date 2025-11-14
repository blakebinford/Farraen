from django import forms

from drive.models import FileNode

from .models import MaterialHeat


class MaterialHeatForm(forms.ModelForm):
    REQUIRED_FIELDS = (
        "heat_number",
        "description",
        "material_grade",
        "outer_diameter_in",
        "wall_thickness_in",
    )

    class Meta:
        model = MaterialHeat
        fields = [
            "heat_number",
            "description",
            "material_grade",
            "outer_diameter_in",
            "wall_thickness_in",
            "wps_number",
            "mtr_document",
            "is_active",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 2}),
            "outer_diameter_in": forms.NumberInput(attrs={"step": "0.001", "min": "0"}),
            "wall_thickness_in": forms.NumberInput(attrs={"step": "0.001", "min": "0"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in self.REQUIRED_FIELDS:
            self.fields[field_name].required = True
        self.fields["heat_number"].label = "Heat number"
        self.fields["material_grade"].label = "Material grade"
        self.fields["outer_diameter_in"].label = "Outer diameter (in)"
        self.fields["wall_thickness_in"].label = "Wall thickness (in)"
        self.fields["wps_number"].label = "Associated WPS number"
        self.fields["wps_number"].help_text = "Optional"
        self.fields["mtr_document"].label = "Associated MTR"
        approved_mtrs = FileNode.objects.filter(
            doc_type=FileNode.DocType.MTR,
            mtr_approved=True,
        ).order_by("name")
        self.fields["mtr_document"].queryset = approved_mtrs
        self.fields["mtr_document"].help_text = (
            "Only approved MTR documents are available. Contact QA if the expected MTR is missing."
        )
        self.fields["is_active"].help_text = (
            "Inactive heats are hidden from the weld log heat selection list."
        )
        for field in self.fields.values():
            widget = field.widget
            if getattr(widget, "input_type", None) not in {"checkbox", "radio"}:
                existing = widget.attrs.get("class", "")
                widget.attrs["class"] = f"form-control {existing}".strip()
