"""Forms for the Admin and Staff tabs."""

from django import forms

from moontax.models import Configuration, NotificationSetting, OreTaxRate, OreType


class ConfigurationForm(forms.ModelForm):
    class Meta:
        model = Configuration
        # target_corporation is intentionally omitted: it is derived automatically from
        # the registered corp token (the token character's corporation), not typed in.
        fields = [
            "default_tax_rate",
            "despawn_hours",
            "fuel_low_days",
            "reminder_every_days",
            "reminder_daily_after_days",
            "table_page_size",
        ]
        widgets = {
            f: forms.NumberInput(attrs={"class": "form-control form-control-sm"})
            for f in fields
        }
        help_texts = {
            "default_tax_rate": "Fraction, e.g. 0.10 for 10%.",
            "despawn_hours": "Hours after fracture before a pop is finalized.",
            "fuel_low_days": "Highlight structures with less than this many days of fuel.",
            "table_page_size": "Rows per page in tables (default 25).",
        }


class OreTaxRateForm(forms.ModelForm):
    # Overrides are restricted to the ESI-sourced moon-ore catalog (OreType). Picking
    # from that fixed list means the id/name can't drift apart, so ore_type_name is
    # derived from the selection rather than typed in.
    ore_type_id = forms.TypedChoiceField(
        label="Ore",
        coerce=int,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )

    class Meta:
        model = OreTaxRate
        fields = ["ore_type_id", "rate"]
        widgets = {
            "rate": forms.NumberInput(
                attrs={"class": "form-control form-control-sm", "step": "0.0001"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ores = OreType.objects.choices()
        self._ore_names = dict(ores)
        self.fields["ore_type_id"].choices = [("", "— Select ore —")] + ores

    def clean(self):
        cleaned = super().clean()
        ore_type_id = cleaned.get("ore_type_id")
        if ore_type_id is not None:
            cleaned["ore_type_name"] = self._ore_names.get(ore_type_id, "")
        return cleaned


class NotificationSettingForm(forms.ModelForm):
    class Meta:
        model = NotificationSetting
        fields = ["moon_pop", "moon_dead", "invoice_emitted"]
        widgets = {f: forms.CheckboxInput(attrs={"class": "form-check-input"}) for f in fields}


class StaffActionForm(forms.Form):
    """A staff payment action; the comment is mandatory (Requirements §6/§8.2)."""

    comment = forms.CharField(
        widget=forms.Textarea(
            attrs={"rows": 2, "class": "form-control form-control-sm", "required": True}
        )
    )
