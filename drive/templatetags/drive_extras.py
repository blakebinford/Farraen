# drive/templatetags/drive_extras.py
from django import template
from django.utils.timezone import localtime

register = template.Library()

@register.filter
def human_filesize(size):
    """Convert bytes to a human-readable string (GB, MB, KB)."""
    try:
        size = float(size or 0)
    except (TypeError, ValueError):
        return "0 B"
    if size >= 1024**3:
        return f"{size / (1024**3):.2f} GB"
    if size >= 1024**2:
        return f"{size / (1024**2):.2f} MB"
    if size >= 1024:
        return f"{size / 1024:.2f} KB"
    return f"{size:.0f} B"

@register.filter
def file_icon(content_type_or_kind):
    """
    Return a modern icon class name based on content type or 'folder'.
    Uses Bootstrap Icons (bi) by default; swap for your icon set if you like.
    """
    ct = (content_type_or_kind or "").lower()
    if ct == "folder":
        return "bi-folder-fill"
    if "pdf" in ct:
        return "bi-filetype-pdf"
    if "excel" in ct or "spreadsheet" in ct or ct.endswith((".xlsx", ".xls")):
        return "bi-filetype-xls"
    if "word" in ct or ct.endswith((".doc", ".docx")):
        return "bi-filetype-doc"
    if "powerpoint" in ct or ct.endswith((".ppt", ".pptx")):
        return "bi-filetype-ppt"
    if ct.startswith("image/") or ct.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff")):
        return "bi-file-earmark-image"
    if ct.startswith("video/"):
        return "bi-file-earmark-play"
    if ct.startswith("audio/"):
        return "bi-file-earmark-music"
    if ct in ("text/plain",) or ct.endswith(".txt"):
        return "bi-filetype-txt"
    if "zip" in ct or ct.endswith((".zip", ".rar", ".7z", ".tar", ".gz")):
        return "bi-file-earmark-zip"
    return "bi-file-earmark"

@register.filter
def modified_when(obj):
    """
    Returns a datetime for last modified:
    - File: use latest version.created_at
    - Folder: use folder.updated_at (if you have one) or created_at fallback
    """
    # FileNode path
    fv = getattr(obj, "latest_version", None)
    if fv:
        return localtime(fv.created_at)
    # Folder path (adjust to your model fields)
    dt = getattr(obj, "updated_at", None) or getattr(obj, "created_at", None)
    return localtime(dt) if dt else None

@register.filter
def modified_who(obj):
    """
    Returns a display name for who modified last:
    - File: latest version uploaded_by
    - Folder: None (or owner/creator if you track that)
    """
    fv = getattr(obj, "latest_version", None)
    if fv and fv.uploaded_by:
        # Prefer full name, fallback to email/username
        return fv.uploaded_by.get_full_name() or fv.uploaded_by.email or fv.uploaded_by.username
    return ""
