from pathlib import Path

from django import forms
from django.utils import timezone

from .importer import SUPPORTED_SUFFIXES
from .models import Correspondence, Attachment, Action, CollectionRecord, Branch, Position


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
        exclude = [
            "reference_id", "original_row", "created_by", "created_at", "updated_at",
            "supervisor_directives",
        ]
        labels = {
            "date_received_sent": "Date received / sent",
            "copy_to_thru": "Copy to / thru",
            "external_reference": "Lab / reference / invoice no.",
            "received_collected_by": "Received / collected by",
            "date_received_collected": "Date received / collected",
            "origin": "Origin",
            "origin_external_name": "External origin — name",
            "sender": "From (Delivery) — name",
            "sender_branch": "From (Delivery) — branch",
            "sender_position": "From (Delivery) — position",
            "recipient": "To (Delivery) — name",
            "recipient_branch": "To (Delivery) — branch",
            "recipient_position": "To (Delivery) — position",
            "from_addressee": "From",
            "to_addressee": "To",
        }
        widgets = {
            "date_received_sent": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "date_of_document": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "date_received_collected": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            # Model TextFields that hold a single line render as oversized
            # textareas by default, so they are shown as plain inputs.
            "origin": forms.Select(attrs={"data-origin-select": "1"}),
            "sender": forms.TextInput(),
            "sender_branch": forms.Select(),
            "sender_position": forms.Select(),
            "recipient": forms.TextInput(),
            "recipient_branch": forms.Select(),
            "recipient_position": forms.Select(),
            "from_addressee": forms.TextInput(),
            "to_addressee": forms.TextInput(),
            "copy_to_thru": forms.TextInput(),
            "external_reference": forms.TextInput(),
            "received_collected_by": forms.TextInput(),
            "description": forms.Textarea(attrs={"rows": 3}),
            "further_comments": forms.Textarea(attrs={"rows": 3}),
            "origin_external_name": forms.TextInput(
                attrs={
                    "placeholder": "Name of the external person/organisation",
                    "data-origin-external-name": "1",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Get all admin-managed branches and positions
        managed_branches = {b.name for b in Branch.objects.all()}
        managed_positions = {p.name for p in Position.objects.all()}
        
        # Build branch choices, including current values if they exist in the instance
        branch_choices = [("", "---------")]
        branch_names = set(managed_branches)
        if self.instance and self.instance.pk:
            # Add current values to the set if they're not in managed list
            if self.instance.sender_branch and self.instance.sender_branch not in managed_branches:
                branch_names.add(self.instance.sender_branch)
            if self.instance.recipient_branch and self.instance.recipient_branch not in managed_branches:
                branch_names.add(self.instance.recipient_branch)
        branch_choices += [(name, name) for name in sorted(branch_names)]
        
        # Build position choices, including current values if they exist in the instance
        position_choices = [("", "---------")]
        position_names = set(managed_positions)
        if self.instance and self.instance.pk:
            # Add current values to the set if they're not in managed list
            if self.instance.sender_position and self.instance.sender_position not in managed_positions:
                position_names.add(self.instance.sender_position)
            if self.instance.recipient_position and self.instance.recipient_position not in managed_positions:
                position_names.add(self.instance.recipient_position)
        position_choices += [(name, name) for name in sorted(position_names)]
        
        for field_name in ("sender_branch", "recipient_branch"):
            self.fields[field_name].widget.choices = branch_choices
        for field_name in ("sender_position", "recipient_position"):
            self.fields[field_name].widget.choices = position_choices


class AttachmentForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Attachment
        fields = ["file", "notes"]


class ActionForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Action
        fields = ["action_text", "action_date", "status"]
        widgets = {
            "action_text": forms.Textarea(attrs={"rows": 2, "placeholder": "Directive or action taken"}),
            "action_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # When adding new actions, fields can be optional with defaults applied.
        # When editing existing actions, preserve required-field behavior so users
        # cannot accidentally clear dates/status and have them silently replaced.
        is_adding = self.instance.pk is None
        if is_adding:
            for field_name in ("action_text", "action_date", "status"):
                self.fields[field_name].required = False
        self.fields["status"].choices = [("", "---------")] + list(Action.Status.choices)

    def save(self, commit=True):
        instance = super().save(commit=False)
        if not instance.action_date:
            instance.action_date = timezone.localdate()
        if not instance.status:
            instance.status = Action.Status.OPEN
        if commit:
            instance.save()
        return instance


class CollectionRecordForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = CollectionRecord
        fields = ["collected_by", "collection_date", "notes"]
        widgets = {
            "collection_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # None of these are mandatory: an entry can be logged with partial
        # information and filled in later.
        for field_name in ("collected_by", "collection_date"):
            self.fields[field_name].required = False

    def save(self, commit=True):
        instance = super().save(commit=False)
        if not instance.collection_date:
            instance.collection_date = timezone.localdate()
        if commit:
            instance.save()
        return instance



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
