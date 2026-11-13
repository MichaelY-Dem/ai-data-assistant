"""Uploaded documents: validation, text extraction and Azure Blob Storage.

Storage layout in the private "documents" container:
    files/<id>/<file name>   the original upload (PDF or Excel)
    extracted/<id>.json      metadata + extracted text, one section per PDF page or Excel sheet

The listing reads only blob metadata on extracted/<id>.json, so it never downloads text.

The connection string comes from AZURE_STORAGE_CONNECTION_STRING in .env. For the local
Azurite emulator use "UseDevelopmentStorage=true".

Everything extracted from a document is untrusted: render_documents_for_prompt() escapes it and
wraps it in <documents> tags so prompts can tell Claude to treat it as data only.
"""
from __future__ import annotations

import datetime as dt
import io
import itertools
import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import PurePath

CONTAINER_NAME = "documents"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024          # 10 MB
MAX_EXTRACTED_CHARS = 300_000                # stored text per document
MAX_EXCEL_ROWS_PER_SHEET = 5_000
COMPANY_CODE_RE = re.compile(r"^CO-\d{3}$")

# extension -> (kind, content type, magic bytes the file must start with)
FILE_TYPES = {
    ".pdf": ("pdf", "application/pdf", b"%PDF-"),
    ".xlsx": ("excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", b"PK\x03\x04"),
}


class DocumentError(Exception):
    """A problem to report to the client with an HTTP status code."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


@dataclass
class Section:
    label: str      # "p.2" for a PDF page, "sheet Budget" for an Excel sheet
    text: str


@dataclass
class Document:
    id: str
    filename: str
    kind: str                   # "pdf" or "excel"
    content_type: str
    size_bytes: int
    company_code: str | None
    uploaded_at: str            # ISO 8601, UTC
    sections: list[Section] = field(default_factory=list)
    truncated: bool = False

    @property
    def char_count(self) -> int:
        return sum(len(s.text) for s in self.sections)

    def summary(self) -> dict:
        """Metadata only, as returned by GET /documents."""
        return {
            "id": self.id,
            "filename": self.filename,
            "kind": self.kind,
            "size_bytes": self.size_bytes,
            "company_code": self.company_code,
            "uploaded_at": self.uploaded_at,
            "section_count": len(self.sections),
            "char_count": self.char_count,
            "truncated": self.truncated,
        }


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

def safe_filename(filename: str | None) -> str:
    """Base name only, ASCII letters/digits/._- (spaces become _), at most 120 chars."""
    name = PurePath((filename or "").replace("\\", "/")).name
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "document"
    ext = re.sub(r"[^A-Za-z0-9]", "", ext).lower()
    return (stem[:110] + (f".{ext}" if ext else ""))


def validate_upload(filename: str | None, data: bytes) -> tuple[str, str, str]:
    """Check type and size. Returns (safe file name, kind, content type) or raises DocumentError."""
    name = safe_filename(filename)
    ext = PurePath(name).suffix.lower()
    if ext not in FILE_TYPES:
        raise DocumentError(415, f"Unsupported file type {ext or '(none)'}: upload a PDF (.pdf) or Excel (.xlsx) file.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise DocumentError(413, f"File is too large: the limit is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    if not data:
        raise DocumentError(422, "The file is empty.")
    kind, content_type, magic = FILE_TYPES[ext]
    if not data.startswith(magic):
        raise DocumentError(415, f"The file content is not a valid {ext} file.")
    return name, kind, content_type


def validate_company_code(code: str | None) -> str | None:
    code = (code or "").strip().upper()
    if not code:
        return None
    if not COMPANY_CODE_RE.fullmatch(code):
        raise DocumentError(422, "company_code must look like CO-001.")
    return code


# --------------------------------------------------------------------------- #
# Text extraction
# --------------------------------------------------------------------------- #

def extract_pdf(data: bytes) -> list[Section]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise DocumentError(422, "The PDF is password protected.")
        return [Section(f"p.{n}", _clean(page.extract_text() or "")) for n, page in enumerate(reader.pages, 1)]
    except DocumentError:
        raise
    except Exception as exc:  # pypdf raises many different error types on broken files
        raise DocumentError(422, f"Could not read the PDF: {exc}") from exc


def extract_excel(data: bytes) -> list[Section]:
    from openpyxl import load_workbook

    try:
        # Cached values first; files written by scripts (not Excel) have no cached values for
        # formulas, so fall back to the formula text for those cells.
        values = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        formulas = load_workbook(io.BytesIO(data), read_only=True, data_only=False)
        sections = []
        for ws in values.worksheets:
            lines = []
            rows = itertools.zip_longest(ws.iter_rows(values_only=True),
                                         formulas[ws.title].iter_rows(values_only=True), fillvalue=())
            for row_values, row_formulas in itertools.islice(rows, MAX_EXCEL_ROWS_PER_SHEET):
                cells = [_cell_text(v if v is not None else f)
                         for v, f in itertools.zip_longest(row_values, row_formulas)]
                while cells and not cells[-1]:
                    cells.pop()
                if any(cells):
                    lines.append(" | ".join(cells))
            sections.append(Section(f"sheet {ws.title}", "\n".join(lines)))
        values.close()
        formulas.close()
        return sections
    except Exception as exc:
        raise DocumentError(422, f"Could not read the Excel file: {exc}") from exc


def extract_text(kind: str, data: bytes) -> tuple[list[Section], bool]:
    """Returns (sections, truncated). Caps the total text at MAX_EXTRACTED_CHARS."""
    sections = extract_pdf(data) if kind == "pdf" else extract_excel(data)
    if not any(s.text.strip() for s in sections):
        raise DocumentError(422, "No text could be extracted (is it a scanned PDF or an empty workbook?).")
    budget, out, truncated = MAX_EXTRACTED_CHARS, [], False
    for s in sections:
        if len(s.text) > budget:
            out.append(Section(s.label, s.text[:budget]))
            truncated = True
            break
        out.append(s)
        budget -= len(s.text)
    return out, truncated


def _cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else f"{value:.2f}".rstrip("0").rstrip(".")
    if isinstance(value, dt.datetime) and value.time() == dt.time():
        return value.date().isoformat()
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    return str(value).strip()


def _clean(text: str) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)  # control characters (e.g. PDF bullets)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def new_document(filename: str, kind: str, content_type: str, data: bytes,
                 company_code: str | None) -> Document:
    sections, truncated = extract_text(kind, data)
    return Document(
        id=uuid.uuid4().hex,
        filename=filename,
        kind=kind,
        content_type=content_type,
        size_bytes=len(data),
        company_code=company_code,
        uploaded_at=dt.datetime.now(dt.timezone.utc).isoformat(),  # microseconds keep the list order stable
        sections=sections,
        truncated=truncated,
    )


# --------------------------------------------------------------------------- #
# Prompt rendering: document text is data, never instructions
# --------------------------------------------------------------------------- #

def _escape(text: str) -> str:
    """Escape <, > and & so document text can't close or fake our tags."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_documents_for_prompt(docs: list[Document], max_chars_per_doc: int = 120_000) -> str:
    if not docs:
        return "<documents>\n(no documents uploaded)\n</documents>"
    parts = ["<documents>"]
    for doc in docs:
        parts.append(f'<document file="{_escape(doc.filename)}" type="{doc.kind}">')
        budget = max_chars_per_doc
        for s in doc.sections:
            text = s.text[:budget]
            budget -= len(text)
            cite = f"[{doc.filename}, {s.label}]"
            parts.append(f'<section cite="{_escape(cite)}">\n{_escape(text) or "(no text on this page)"}\n</section>')
            if budget <= 0:
                parts.append("<note>The rest of this document was cut to fit.</note>")
                break
        parts.append("</document>")
    parts.append("</documents>")
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Azure Blob Storage
# --------------------------------------------------------------------------- #

def _meta_encode(doc: Document) -> dict[str, str]:
    """Blob metadata must be ASCII strings; file names are already ASCII (safe_filename)."""
    return {k: "" if v is None else str(v) for k, v in doc.summary().items()}


def _meta_decode(meta: dict[str, str]) -> dict:
    return {
        "id": meta.get("id", ""),
        "filename": meta.get("filename", ""),
        "kind": meta.get("kind", ""),
        "size_bytes": int(meta.get("size_bytes") or 0),
        "company_code": meta.get("company_code") or None,
        "uploaded_at": meta.get("uploaded_at", ""),
        "section_count": int(meta.get("section_count") or 0),
        "char_count": int(meta.get("char_count") or 0),
        "truncated": meta.get("truncated") == "True",
    }


class DocumentStore:
    """Blocking Azure SDK calls: run them in a worker thread from async code."""

    def __init__(self, container_client):
        self.container = container_client

    @classmethod
    def from_env(cls) -> "DocumentStore":
        from azure.storage.blob import BlobServiceClient

        conn = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
        if not conn:
            raise DocumentError(503, "Document storage is not configured: set AZURE_STORAGE_CONNECTION_STRING in .env.")
        # Few retries and a short connect timeout: fail fast when Azurite isn't running.
        service = BlobServiceClient.from_connection_string(conn, retry_total=2, connection_timeout=5)
        return cls(service.get_container_client(CONTAINER_NAME))

    def ensure_container(self) -> None:
        """Create the container if missing. No public_access argument = private."""
        from azure.core.exceptions import ResourceExistsError

        try:
            self.container.create_container()
        except ResourceExistsError:
            pass

    def save(self, doc: Document, data: bytes) -> None:
        from azure.storage.blob import ContentSettings

        self.container.upload_blob(
            f"files/{doc.id}/{doc.filename}", data, overwrite=True,
            content_settings=ContentSettings(content_type=doc.content_type),
            metadata={"id": doc.id, "company_code": doc.company_code or ""},
        )
        payload = asdict(doc)
        self.container.upload_blob(
            f"extracted/{doc.id}.json", json.dumps(payload, ensure_ascii=False).encode("utf-8"), overwrite=True,
            content_settings=ContentSettings(content_type="application/json"),
            metadata=_meta_encode(doc),
        )

    def list(self) -> list[dict]:
        docs = [_meta_decode(b.metadata or {})
                for b in self.container.list_blobs(name_starts_with="extracted/", include=["metadata"])]
        return sorted(docs, key=lambda d: d["uploaded_at"], reverse=True)

    def get(self, doc_id: str) -> Document:
        from azure.core.exceptions import ResourceNotFoundError

        if not re.fullmatch(r"[0-9a-f]{32}", doc_id or ""):
            raise DocumentError(404, "Document not found.")
        try:
            raw = self.container.download_blob(f"extracted/{doc_id}.json").readall()
        except ResourceNotFoundError:
            raise DocumentError(404, "Document not found.") from None
        payload = json.loads(raw)
        payload["sections"] = [Section(**s) for s in payload.get("sections", [])]
        return Document(**payload)

    def for_company(self, company_code: str) -> list[Document]:
        return [self.get(d["id"]) for d in self.list() if d["company_code"] == company_code]
