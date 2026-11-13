from django import forms
from .models import Correspondence, Attachment, Action, CollectionRecord

class CorrespondenceForm(forms.ModelForm):
    class Meta:
        model = Correspondence
        exclude = ["reference_id", "original_row", "created_by", "created_at", "updated_at"]
        widgets = {
            "date_received_sent": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "date_of_document": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "date_received_collected": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "description": forms.Textarea(attrs={"rows": 3}),
            "supervisor_directives": forms.Textarea(attrs={"rows": 3}),
            "further_comments": forms.Textarea(attrs={"rows": 3}),
        }

class AttachmentForm(forms.ModelForm):
    class Meta:
        model = Attachment
        fields = ["file", "notes"]

class ActionForm(forms.ModelForm):
    class Meta:
        model = Action
        fields = ["action_text", "action_date", "assigned_to"]
        widgets = {"action_date": forms.DateInput(attrs={"type": "date"})}

class CollectionRecordForm(forms.ModelForm):
    class Meta:
        model = CollectionRecord
        fields = ["collected_by", "collection_date", "notes"]
        widgets = {"collection_date": forms.DateInput(attrs={"type": "date"})}
