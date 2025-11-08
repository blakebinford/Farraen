import re
from typing import Optional, Tuple, Iterable
from django.conf import settings
from .models import FileEvent, FileNode, FileVersion

def _client_ip_and_ua(request) -> Tuple[Optional[str], str]:
    """
    Prefer first hop from X-Forwarded-For if present; fall back to REMOTE_ADDR.
    UA capped to 512 chars.
    """
    ip = None
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        # "client, proxy1, proxy2"
        ip = (xff.split(",")[0] or "").strip() or None
    if not ip:
        ip = request.META.get("REMOTE_ADDR")
    ua = (request.META.get("HTTP_USER_AGENT") or "")[:512]
    return ip, ua

def log_file_event(request, node: FileNode, action: str, version: Optional[FileVersion] = None):
    ip, ua = _client_ip_and_ua(request)
    FileEvent.objects.create(
        org=request.org,
        file_node=node,
        version=version,
        actor=request.user if request.user.is_authenticated else None,
        action=action,
        ip=ip,
        ua=ua,
    )

SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

def sanitize_filename(name: str, max_len: int = 120) -> str:
    """
    Strip control chars/unsafe bytes; collapse spaces; keep extension.
    We do not transliterate, just constrain to a safe subset for storage keys.
    """
    name = name.strip()
    # split ext safely
    if "." in name:
        stem, ext = name.rsplit(".", 1)
        ext = "." + ext
    else:
        stem, ext = name, ""
    # collapse whitespace
    stem = re.sub(r"\s+", "_", stem)
    # remove any non-safe chars
    stem = SAFE_FILENAME_RE.sub("_", stem)
    # guard empty
    stem = stem or "file"
    # trim
    stem = stem[: max_len - len(ext)]
    return f"{stem}{ext}"

def sniff_mime(upload, fallback: Optional[str] = None) -> str:
    """
    Light-weight sniff: trust content_type if present; fall back to filename guess;
    for PDFs/images do a magic header peek (without consuming the stream).
    """
    ctype = getattr(upload, "content_type", None) or fallback or "application/octet-stream"

    # filename-based hint as a weak signal
    try:
        import mimetypes
        guess, _ = mimetypes.guess_type(getattr(upload, "name", ""))
        if guess:
            ctype = guess
    except Exception:
        pass

    # small magic header peek
    try:
        pos = upload.tell()
    except Exception:
        pos = None
    try:
        upload.seek(0)
        head = upload.read(8)
    finally:
        try:
            if pos is not None:
                upload.seek(pos)
        except Exception:
            pass

    if head.startswith(b"%PDF"):
        return "application/pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"

    return ctype

def validate_upload(upload, allowed: Iterable[str], max_mb: int) -> Tuple[bool, str, str]:
    """
    Returns (ok, content_type, error_message_if_any)
    """
    size = getattr(upload, "size", None) or 0
    if size > max_mb * 1024 * 1024:
        return (False, "", f"File exceeds the maximum size of {max_mb} MB.")

    ctype = sniff_mime(upload, getattr(upload, "content_type", None))
    if ctype not in allowed:
        return (False, ctype, f"Files of type '{ctype}' are not allowed.")
    return (True, ctype, "")