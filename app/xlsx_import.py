import io
import zipfile
from datetime import date, datetime
from pathlib import Path

from app import submit
from app.choices import CHANNELS, PRODUCTS
from app.queue import FILTER_OPTIONS
from app.roles import AFFILIATE_CHANNEL

# Importing submissions from an Excel file. Parsing and checking are pure; the only writes go through
# submit.create_submission, so an imported row gets exactly the validation, caps, duplicate check and
# flags a typed one does. Nothing from a cell is ever used as a message: errors are fixed wording.

SAMPLE_PATH = Path(__file__).resolve().parent.parent / "data" / "import-sample.xlsx"
MAX_ROWS = 200            # data rows that hold something
MAX_SCANNED_ROWS = 2_000  # rows looked at, so a sheet of blank rows cannot keep us busy
MAX_UNZIPPED_BYTES = 20 * 1024 * 1024
MAX_ZIP_MEMBERS = 200

# Header text (lowercase, spaces for underscores) -> the field it fills. Other columns are ignored,
# including any "submitted by": that always comes from who is using the demo, never from a file.
HEADERS = {
    "title": "title",
    "product": "product",
    "channel": "channel",
    "launch date": "launch_date",
    "launch": "launch_date",
    "copy": "copy",
    "asset copy": "copy",
    "notes": "notes",
    "notes for reviewer": "notes",
}
REQUIRED = ("title", "product", "channel", "launch_date", "copy")
COLUMN_NAMES = {"title": "Title", "product": "Product", "channel": "Channel", "launch_date": "Launch date",
                "copy": "Copy", "notes": "Notes"}

FILE_MESSAGES = {
    "empty": "No file was chosen. Choose an .xlsx file, or use the sample file.",
    "not_xlsx": "That isn't an Excel .xlsx file. Save it as .xlsx from Excel and try again.",
    "macros": "Files with macros (.xlsm) can't be imported. Save a copy as .xlsx and try again.",
    "too_big": "That file is too large to import. Keep it under 1 MB.",
    "unreadable": "That file couldn't be read. Open it in Excel, save it as .xlsx and try again.",
    "no_sheet": "That workbook has no sheet to read. Add your rows to the first sheet.",
    "no_rows": "No rows found. Add one row per piece of copy under the header row, then try again.",
    "too_many": "That sheet has more than %d rows. Split it into smaller files." % MAX_ROWS,
    "sample_missing": "The sample file isn't available on this server. Upload your own .xlsx instead.",
}


class ImportFileError(Exception):
    """The file as a whole can't be used. `code` is a key of FILE_MESSAGES, or "missing_columns"."""

    def __init__(self, code, columns=()):
        super().__init__(code)
        self.code = code
        self.columns = tuple(columns)

    @property
    def message(self):
        if self.code == "missing_columns":
            names = ", ".join(COLUMN_NAMES[c] for c in self.columns)
            return "The first row needs these column headers: %s. Add them, then try again." % names
        return FILE_MESSAGES[self.code]


def read_sample():
    try:
        return SAMPLE_PATH.read_bytes()
    except OSError:
        raise ImportFileError("sample_missing") from None


# ---- reading the workbook ----

def _check_container(data):
    """Refuse anything that is not a plain .xlsx zip, before openpyxl sees it."""
    if not data:
        raise ImportFileError("empty")
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise ImportFileError("not_xlsx")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = z.infolist()
            names = {i.filename for i in infos}
            if len(infos) > MAX_ZIP_MEMBERS or sum(i.file_size for i in infos) > MAX_UNZIPPED_BYTES:
                raise ImportFileError("too_big")
            if "xl/vbaProject.bin" in names:
                raise ImportFileError("macros")
            if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
                raise ImportFileError("not_xlsx")
    except zipfile.BadZipFile:
        raise ImportFileError("unreadable") from None


def _canonical(header):
    if not isinstance(header, str):
        return None
    return HEADERS.get(" ".join(header.replace("_", " ").split()).lower())


def _is_blank_cell(value):
    return value is None or (isinstance(value, str) and not value.strip())


def read_rows(data):
    """The data rows of the first sheet as [{"row": sheet row number, "fields": {...raw cell values}}].

    Raises ImportFileError for a file we won't read. Formulas give their last saved value. Rows with
    nothing in any known column are skipped. Cells are returned as read (str, number, date, datetime).
    """
    from openpyxl import load_workbook

    _check_container(data)
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        raise ImportFileError("unreadable") from None
    try:
        if not wb.sheetnames:
            raise ImportFileError("no_sheet")
        sheet = wb[wb.sheetnames[0]]
        header = None
        rows = []
        try:
            for number, values in enumerate(sheet.iter_rows(values_only=True), 1):
                if number > MAX_SCANNED_ROWS:
                    raise ImportFileError("too_many")
                if header is None:
                    if all(_is_blank_cell(v) for v in values):
                        continue  # blank lines above the header
                    header = {}
                    for index, cell in enumerate(values):
                        field = _canonical(cell)
                        if field is not None and field not in header.values():
                            header[index] = field
                    continue
                fields = {field: values[i] if i < len(values) else None for i, field in header.items()}
                if all(_is_blank_cell(v) for v in fields.values()):
                    continue
                if len(rows) >= MAX_ROWS:
                    raise ImportFileError("too_many")
                rows.append({"row": number, "fields": fields})
        except ImportFileError:
            raise
        except Exception:
            raise ImportFileError("unreadable") from None
        if header is None:
            raise ImportFileError("no_rows")
        return header, rows
    finally:
        wb.close()


def parse_workbook(data, need_channel=True):
    """read_rows plus the checks on the headers and the row count. Returns the rows."""
    header, rows = read_rows(data)
    needed = [f for f in REQUIRED if need_channel or f != "channel"]
    missing = [f for f in needed if f not in header.values()]
    if missing:
        raise ImportFileError("missing_columns", missing)
    if not rows:
        raise ImportFileError("no_rows")
    return rows


# ---- turning cells into submission fields ----

_PRODUCT_LOOKUP = {}
_CHANNEL_LOOKUP = {}
for _value in PRODUCTS:
    _PRODUCT_LOOKUP[_value] = _value
for _value, _label in FILTER_OPTIONS["product"]:
    _PRODUCT_LOOKUP[_label.lower()] = _value
for _value in CHANNELS:
    _CHANNEL_LOOKUP[_value] = _value
for _value, _label in FILTER_OPTIONS["channel"]:
    _CHANNEL_LOOKUP[_label.lower()] = _value


def _text(value):
    """Text cells as they are; a number typed into a text column becomes its text; anything else is
    left alone so the validator refuses it."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    return value


def _choice(value, lookup):
    if not isinstance(value, str):
        return value
    key = " ".join(value.replace("_", " ").split()).lower()
    return lookup.get(key) or lookup.get(key.replace(" ", "_")) or value


def _launch(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return value.strip()
    return None  # a bare number (an Excel serial) or anything else: the validator says to use YYYY-MM-DD


def normalize_row(fields, force_channel=None):
    """Cell values -> the dict validate_submission takes. Never raises."""
    return {
        "title": _text(fields.get("title")),
        "product": _choice(fields.get("product"), _PRODUCT_LOOKUP),
        "channel": force_channel or _choice(fields.get("channel"), _CHANNEL_LOOKUP),
        "launch_date": _launch(fields.get("launch_date")),
        "copy": _text(fields.get("copy")),
        "notes": _text(fields.get("notes")),
    }


# ---- preview and import ----

def _existing(conn, submitter, clean):
    row = conn.execute(
        "SELECT s.id FROM submission s JOIN version v"
        " ON v.submission_id = s.id AND v.version_number = s.current_version"
        " WHERE s.submitted_by = ? AND s.title = ? AND s.product = ? AND s.channel = ? AND v.copy = ?"
        " ORDER BY s.id LIMIT 1",
        (submitter, clean["title"], clean["product"], clean["channel"], clean["copy"])).fetchone()
    return row[0] if row else None


def preview_rows(conn, rows, today, submitter, force_channel=None):
    """Check every row without writing anything.

    Each result: {"row", "status": "ok"|"error"|"duplicate", "title", "errors": [messages],
    "warnings": [codes], "cards": [flag cards], "existing_id"}. A duplicate is an exact repeat of an
    existing submission by this submitter, or of an earlier row in the same file. The title is shown
    only when it passed validation.
    """
    results, seen = [], set()
    for item in rows:
        fields = normalize_row(item["fields"], force_channel)
        clean, errors, warnings = submit.validate_submission(fields, today)
        result = {"row": item["row"], "status": "ok", "title": clean.get("title"), "errors": list(errors.values()),
                  "warnings": warnings, "cards": [], "existing_id": None, "launch_date": clean.get("launch_date")}
        if errors:
            result["status"] = "error"
        else:
            key = (clean["title"], clean["product"], clean["channel"], clean["copy"])
            existing = _existing(conn, submitter, clean)
            if existing is not None or key in seen:
                result["status"] = "duplicate"
                result["existing_id"] = existing
            else:
                seen.add(key)
                check = submit.precheck(clean["product"], clean["channel"], clean["copy"])
                result["cards"] = check.get("cards", [])
        results.append(result)
    return results


def import_rows(conn, rows, submitter, now, force_channel=None):
    """Create a submission per valid row. Returns {"created", "skipped", "failed", "stopped"}.

    Each row is its own transaction (create_submission commits), so a failure never leaves half a
    submission, but earlier rows stay created: the import is not all-or-nothing. A duplicate is
    skipped; a full demo stops the import (`stopped`); any other refusal counts as failed.
    """
    summary = {"created": 0, "skipped": 0, "failed": 0, "stopped": False}
    for item in rows:
        fields = normalize_row(item["fields"], force_channel)
        clean, errors, _ = submit.validate_submission(fields, now.date())
        if errors:
            summary["failed"] += 1
            continue
        try:
            submit.create_submission(conn, clean, submitter, now)
            summary["created"] += 1
        except submit.SubmitError as exc:
            if exc.code == "duplicate":
                summary["skipped"] += 1
            elif exc.code == "capacity":
                summary["stopped"] = True
                break
            else:
                summary["failed"] += 1
    return summary


def force_channel_for(role):
    return AFFILIATE_CHANNEL if role == "affiliate" else None
