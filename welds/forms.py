from django import forms

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
        self.fields["is_active"].help_text = (
            "Inactive heats are hidden from the weld log heat selection list."
        )
