from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET


ALLOWED_TEXT_ATTACHMENT_EXTENSIONS = {".docx", ".xlsx", ".txt", ".md", ".log"}
MAX_PARSED_TEXT_CHARS = 50_000


@dataclass(frozen=True)
class ParsedAttachmentText:
    text: str
    truncated: bool = False


def parse_attachment_text(filename: str, data: bytes) -> ParsedAttachmentText:
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_TEXT_ATTACHMENT_EXTENSIONS:
        raise ValueError("Unsupported attachment type")

    if extension == ".docx":
        return _truncate_text(_parse_docx(data))
    if extension == ".xlsx":
        return _truncate_text(_parse_xlsx(data))
    return _truncate_text(_decode_text(data))


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _parse_docx(data: bytes) -> str:
    lines: list[str] = []
    with zipfile.ZipFile(BytesIO(data)) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    for paragraph in root.iter(_tag("p")):
        text = "".join(node.text or "" for node in paragraph.iter(_tag("t"))).strip()
        if text:
            lines.append(text)
    return "\n".join(lines).strip()


def _parse_xlsx(data: bytes) -> str:
    with zipfile.ZipFile(BytesIO(data)) as archive:
        shared_strings = _read_xlsx_shared_strings(archive)
        sheet_names = _read_xlsx_sheet_names(archive)
        sheet_files = sorted(name for name in archive.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"))

    lines: list[str] = []
    with zipfile.ZipFile(BytesIO(data)) as archive:
        for sheet_index, sheet_file in enumerate(sheet_files):
            sheet_name = sheet_names[sheet_index] if sheet_index < len(sheet_names) else Path(sheet_file).stem
            lines.append(f"Sheet: {sheet_name}")
            root = ET.fromstring(archive.read(sheet_file))
            for row_index, row in enumerate(root.iter(_xlsx_tag("row")), start=1):
                values = [_xlsx_cell_text(cell, shared_strings) for cell in row.iter(_xlsx_tag("c"))]
                while values and not values[-1]:
                    values.pop()
                if values:
                    lines.append(" | ".join(values))
                if row_index >= 200:
                    lines.append("... sheet rows truncated ...")
                    break
    return "\n".join(lines).strip()


def _read_xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return ["".join(node.text or "" for node in item.iter(_xlsx_tag("t"))) for item in root.iter(_xlsx_tag("si"))]


def _read_xlsx_sheet_names(archive: zipfile.ZipFile) -> list[str]:
    if "xl/workbook.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/workbook.xml"))
    return [sheet.attrib.get("name", "") for sheet in root.iter(_xlsx_tag("sheet"))]


def _xlsx_cell_text(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(_xlsx_tag("t"))).strip()
    value_node = cell.find(_xlsx_tag("v"))
    if value_node is None or value_node.text is None:
        return ""
    value = value_node.text.strip()
    if cell_type == "s" and value.isdigit():
        index = int(value)
        return shared_strings[index].strip() if index < len(shared_strings) else ""
    return value


def _tag(name: str) -> str:
    return f"{{http://schemas.openxmlformats.org/wordprocessingml/2006/main}}{name}"


def _xlsx_tag(name: str) -> str:
    return f"{{http://schemas.openxmlformats.org/spreadsheetml/2006/main}}{name}"


def _truncate_text(text: str) -> ParsedAttachmentText:
    normalized = text.replace("\x00", "").strip()
    if len(normalized) <= MAX_PARSED_TEXT_CHARS:
        return ParsedAttachmentText(text=normalized, truncated=False)
    return ParsedAttachmentText(text=normalized[:MAX_PARSED_TEXT_CHARS], truncated=True)
