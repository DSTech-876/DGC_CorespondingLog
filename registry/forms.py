from pathlib import Path

from django import forms

from .importer import SUPPORTED_SUFFIXES
from .models import Correspondence, Attachment, Action, CollectionRecord


class BootstrapFormMixin:
    """Applies Bootstrap 5 widget classes so every form matches the UI theme.

    Templates render fields individually, so the classes are attached to the
    widgets here rather than being repeated in markup.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, (forms.CheckboxInput, forms.RadioSelect)):
                css = "form-check-input"
            elif isinstance(widget, forms.CheckboxSelectMultiple):
                css = "form-check-input"
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                css = "form-select"
            elif isinstance(widget, forms.FileInput):
                css = "form-control"
            else:
                css = "form-control"
            existing = widget.attrs.get("class", "")
            widget.attrs["class"] = f"{existing} {css}".strip()


class CorrespondenceForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Correspondence
        exclude = ["reference_id", "original_row", "created_by", "created_at", "updated_at"]
        labels = {
            "date_received_sent": "Date received / sent",
            "copy_to_thru": "Copy to / thru",
            "external_reference": "Lab / reference / invoice no.",
            "received_collected_by": "Received / collected by",
            "date_received_collected": "Date received / collected",
            "sender": "From",
            "recipient": "To",
        }
        widgets = {
            "date_received_sent": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "date_of_document": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "date_received_collected": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            # Model TextFields that hold a single line render as oversized
            # textareas by default, so they are shown as plain inputs.
            "sender": forms.TextInput(),
            "recipient": forms.TextInput(),
            "copy_to_thru": forms.TextInput(),
            "external_reference": forms.TextInput(),
            "received_collected_by": forms.TextInput(),
            "description": forms.Textarea(attrs={"rows": 3}),
            "supervisor_directives": forms.Textarea(attrs={"rows": 3}),
            "further_comments": forms.Textarea(attrs={"rows": 3}),
        }


class AttachmentForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Attachment
        fields = ["file", "notes"]


class ActionForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Action
        fields = ["action_text", "action_date", "assigned_to"]
        widgets = {
            "action_text": forms.Textarea(attrs={"rows": 2, "placeholder": "Directive or action taken"}),
            "action_date": forms.DateInput(attrs={"type": "date"}),
        }


class CollectionRecordForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = CollectionRecord
        fields = ["collected_by", "collection_date", "notes"]
        widgets = {
            "collection_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }


class RegisterImportForm(BootstrapFormMixin, forms.Form):
    """Upload step of the register import wizard."""

    MAX_UPLOAD_BYTES = 25 * 1024 * 1024

    file = forms.FileField(
        label="Register file",
        help_text="Excel workbook (.xlsx), CSV or Markdown table exported from the correspondence log.",
        widget=forms.ClearableFileInput(attrs={"accept": ",".join(SUPPORTED_SUFFIXES)}),
    )
    direction = forms.ChoiceField(
        label="Direction for imported rows",
        choices=Correspondence.Direction.choices,
        initial=Correspondence.Direction.OTHER,
        help_text="Historical registers rarely state direction; it can be corrected per record afterwards.",
    )
    skip_existing = forms.BooleanField(
        label="Skip rows already in the register",
        required=False,
        initial=True,
        help_text="Matches on date received/sent, from, to and description.",
    )

    def clean_file(self):
        upload = self.cleaned_data["file"]
        suffix = Path(upload.name).suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise forms.ValidationError(
                "Unsupported file type. Use one of: " + ", ".join(SUPPORTED_SUFFIXES)
            )
        if upload.size > self.MAX_UPLOAD_BYTES:
            raise forms.ValidationError("The file is larger than the 25 MB upload limit.")
        return upload
