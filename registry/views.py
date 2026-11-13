import csv
from pathlib import Path
from django.http import FileResponse, Http404
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.db.models import Q, Count
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from .models import Correspondence, AuditLog, Attachment
from .forms import CorrespondenceForm, AttachmentForm, ActionForm, CollectionRecordForm

@login_required
def dashboard(request):
    qs = Correspondence.objects.all()
    context = {
        "total": qs.count(),
        "incoming": qs.filter(direction="IN").count(),
        "outgoing": qs.filter(direction="OUT").count(),
        "recent": qs[:8],
        "with_attachments": qs.annotate(n=Count("attachments")).filter(n__gt=0).count(),
    }
    return render(request, "registry/dashboard.html", context)

@login_required
def correspondence_list(request):
    qs = Correspondence.objects.all()
    q = request.GET.get("q", "").strip()
    direction = request.GET.get("direction", "")
    start = request.GET.get("start", "")
    end = request.GET.get("end", "")
    if q:
        qs = qs.filter(
            Q(reference_id__icontains=q) | Q(sender__icontains=q) |
            Q(recipient__icontains=q) | Q(copy_to_thru__icontains=q) |
            Q(description__icontains=q) | Q(external_reference__icontains=q) |
            Q(supervisor_directives__icontains=q) | Q(received_collected_by__icontains=q)
        )
    if direction in ["IN", "OUT", "OTHER"]:
        qs = qs.filter(direction=direction)
    if start: qs = qs.filter(date_received_sent__gte=start)
    if end: qs = qs.filter(date_received_sent__lte=end)
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    return render(request, "registry/list.html", {"page": page, "q": q, "direction": direction, "start": start, "end": end})

@login_required
def correspondence_detail(request, pk):
    item = get_object_or_404(Correspondence, pk=pk)
    return render(request, "registry/detail.html", {"item": item, "attachment_form": AttachmentForm(), "action_form": ActionForm(), "collection_form": CollectionRecordForm()})

@login_required
def correspondence_create(request):
    form = CorrespondenceForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        item = form.save(commit=False); item.created_by = request.user; item.save()
        AuditLog.objects.create(user=request.user, correspondence=item, event="Created", details="Correspondence registered")
        messages.success(request, "Correspondence registered.")
        return redirect("correspondence_detail", pk=item.pk)
    return render(request, "registry/form.html", {"form": form, "title": "Register correspondence"})

@login_required
def correspondence_edit(request, pk):
    item = get_object_or_404(Correspondence, pk=pk)
    form = CorrespondenceForm(request.POST or None, instance=item)
    if request.method == "POST" and form.is_valid():
        form.save()
        AuditLog.objects.create(user=request.user, correspondence=item, event="Updated", details="Correspondence record updated")
        messages.success(request, "Record updated.")
        return redirect("correspondence_detail", pk=item.pk)
    return render(request, "registry/form.html", {"form": form, "title": "Edit correspondence"})

@login_required
def attachment_upload(request, pk):
    item = get_object_or_404(Correspondence, pk=pk)
    form = AttachmentForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False); obj.correspondence = item; obj.uploaded_by = request.user; obj.save()
        AuditLog.objects.create(user=request.user, correspondence=item, event="Attachment uploaded", details=obj.original_filename)
        messages.success(request, "Attachment uploaded.")
    return redirect("correspondence_detail", pk=pk)

@login_required
def action_add(request, pk):
    item = get_object_or_404(Correspondence, pk=pk)
    form = ActionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False); obj.correspondence = item; obj.entered_by = request.user; obj.save()
        AuditLog.objects.create(user=request.user, correspondence=item, event="Action added", details=obj.action_text[:250])
    return redirect("correspondence_detail", pk=pk)

@login_required
def collection_add(request, pk):
    item = get_object_or_404(Correspondence, pk=pk)
    form = CollectionRecordForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False); obj.correspondence = item; obj.recorded_by = request.user; obj.save()
        AuditLog.objects.create(user=request.user, correspondence=item, event="Collection recorded", details=f"{obj.collected_by} — {obj.collection_date}")
    return redirect("correspondence_detail", pk=pk)

@login_required
@permission_required("registry.export_correspondence", raise_exception=True)
def export_csv(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="correspondence-export.csv"'
    writer = csv.writer(response)
    writer.writerow(["System ID", "Direction", "Date Received/Sent", "Date of Document", "From", "To", "Copy To/Thru", "Description", "Lab/Ref/Voucher/Invoice", "Supervisor Directives", "Received/Collected By", "Date Received/Collected", "Further Comments"])
    for x in Correspondence.objects.all():
        writer.writerow([x.reference_id, x.get_direction_display(), x.date_received_sent, x.date_of_document, x.sender, x.recipient, x.copy_to_thru, x.description, x.external_reference, x.supervisor_directives, x.received_collected_by, x.date_received_collected, x.further_comments])
    return response

@login_required
def attachment_download(request, attachment_id):
    attachment = get_object_or_404(Attachment, pk=attachment_id)
    try:
        attachment.file.open("rb")
        file_handle = attachment.file.file
    except (FileNotFoundError, OSError):
        raise Http404("Attachment not found")
    response = FileResponse(file_handle, as_attachment=True, filename=attachment.original_filename or Path(attachment.file.name).name)
    return response
