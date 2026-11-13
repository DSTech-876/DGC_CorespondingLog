import csv
import time
from pathlib import Path
from uuid import uuid4
from django.conf import settings
from django.http import FileResponse, Http404
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.db.models import Q, Count
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from .models import Correspondence, AuditLog, Attachment
from .importer import (
    PREVIEW_LIMIT, ImportError_, flag_duplicates, import_records, parse_rows,
    rows_from_path, rows_from_stream,
)
from .forms import CorrespondenceForm, AttachmentForm, ActionForm, CollectionRecordForm, RegisterImportForm

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
@permission_required("registry.add_correspondence", raise_exception=True)
def correspondence_create(request):
    form = CorrespondenceForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        item = form.save(commit=False); item.created_by = request.user; item.save()
        AuditLog.objects.create(user=request.user, correspondence=item, event="Created", details="Correspondence registered")
        messages.success(request, "Correspondence registered.")
        return redirect("correspondence_detail", pk=item.pk)
    return render(request, "registry/form.html", {"form": form, "title": "Register correspondence"})

@login_required
@permission_required("registry.change_correspondence", raise_exception=True)
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
@permission_required("registry.add_attachment", raise_exception=True)
def attachment_upload(request, pk):
    item = get_object_or_404(Correspondence, pk=pk)
    form = AttachmentForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False); obj.correspondence = item; obj.uploaded_by = request.user; obj.save()
        AuditLog.objects.create(user=request.user, correspondence=item, event="Attachment uploaded", details=obj.original_filename)
        messages.success(request, "Attachment uploaded.")
    return redirect("correspondence_detail", pk=pk)

@login_required
@permission_required("registry.add_action", raise_exception=True)
def action_add(request, pk):
    item = get_object_or_404(Correspondence, pk=pk)
    form = ActionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False); obj.correspondence = item; obj.entered_by = request.user; obj.save()
        AuditLog.objects.create(user=request.user, correspondence=item, event="Action added", details=obj.action_text[:250])
    return redirect("correspondence_detail", pk=pk)

@login_required
@permission_required("registry.add_collectionrecord", raise_exception=True)
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

# ------------------------------------------------------------------ importing
IMPORT_SESSION_KEY = "pending_register_import"


def _staging_dir():
    path = Path(settings.MEDIA_ROOT) / "import_staging"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _stage_upload(upload):
    """Save the upload so the confirm step does not need a second upload."""
    token = f"{uuid4().hex}{Path(upload.name).suffix.lower()}"
    destination = _staging_dir() / token
    with destination.open("wb") as handle:
        for chunk in upload.chunks():
            handle.write(chunk)
    return token


def _staged_path(token):
    """Resolve a staged file token, refusing anything outside the staging dir."""
    if not token or "/" in token or "\\" in token or ".." in token:
        return None
    path = _staging_dir() / token
    return path if path.is_file() else None


def _discard_staged(request):
    pending = request.session.pop(IMPORT_SESSION_KEY, None)
    if not pending:
        return
    path = _staged_path(pending.get("token"))
    if path:
        path.unlink(missing_ok=True)


def _cleanup_old_staging_files(max_age_seconds=86400):
    """Remove staging files older than max_age_seconds (default 24 hours)."""
    staging_dir = _staging_dir()
    current_time = time.time()
    for file_path in staging_dir.iterdir():
        if file_path.is_file():
            file_age = current_time - file_path.stat().st_mtime
            if file_age > max_age_seconds:
                file_path.unlink(missing_ok=True)


@login_required
@permission_required("registry.add_correspondence", raise_exception=True)
def register_import(request):
    """Upload a register file and preview what would be imported."""
    form = RegisterImportForm(request.POST or None, request.FILES or None)
    context = {"form": form}
    if request.method == "POST" and form.is_valid():
        if IMPORT_SESSION_KEY in request.session:
            form.add_error("file", "An import is already in progress. Please cancel or confirm it before uploading another file.")
            return render(request, "registry/import.html", context)
        _cleanup_old_staging_files()
        upload = form.cleaned_data["file"]
        try:
            parsed = parse_rows(rows_from_stream(upload, upload.name))
        except ImportError_ as exc:
            form.add_error("file", str(exc))
            return render(request, "registry/import.html", context)
        if not parsed.records:
            form.add_error("file", "No correspondence rows were found in this file.")
            return render(request, "registry/import.html", context)

        flag_duplicates(parsed)
        token = _stage_upload(upload)
        request.session[IMPORT_SESSION_KEY] = {
            "token": token,
            "filename": upload.name,
            "direction": form.cleaned_data["direction"],
            "skip_existing": form.cleaned_data["skip_existing"],
        }
        duplicates = sum(1 for r in parsed.records if r.duplicate)
        context.update({
            "preview": parsed.records[:PREVIEW_LIMIT],
            "preview_limit": PREVIEW_LIMIT,
            "parsed": parsed,
            "filename": upload.name,
            "duplicates": duplicates,
            "to_import": len(parsed.records) - (duplicates if form.cleaned_data["skip_existing"] else 0),
            "skip_existing": form.cleaned_data["skip_existing"],
            "direction_label": Correspondence.Direction(form.cleaned_data["direction"]).label,
            "warning_records": parsed.warning_records[:PREVIEW_LIMIT],
            "warning_count": len(parsed.warning_records),
        })
    return render(request, "registry/import.html", context)


@login_required
@permission_required("registry.add_correspondence", raise_exception=True)
def register_import_confirm(request):
    """Import the previously previewed file."""
    if request.method != "POST":
        return redirect("register_import")
    pending = request.session.get(IMPORT_SESSION_KEY)
    path = _staged_path(pending.get("token")) if pending else None
    if path is None:
        messages.error(request, "The uploaded file is no longer available. Please upload it again.")
        return redirect("register_import")
    if request.POST.get("action") == "cancel":
        _discard_staged(request)
        messages.info(request, "Import cancelled; nothing was changed.")
        return redirect("register_import")

    try:
        parsed = parse_rows(rows_from_path(path))
        result = import_records(
            parsed,
            user=request.user,
            direction=pending["direction"],
            skip_existing=pending["skip_existing"],
        )
    except ImportError_ as exc:
        messages.error(request, str(exc))
        _discard_staged(request)
        return redirect("register_import")

    AuditLog.objects.create(
        user=request.user,
        event="Register imported",
        details=f"{pending['filename']}: {result.summary}",
    )
    _discard_staged(request)
    messages.success(
        request,
        f"Imported {result.created} record(s) from {pending['filename']}. "
        f"Skipped {result.skipped} duplicate(s).",
    )
    return redirect("correspondence_list")
