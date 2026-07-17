from io import BytesIO
from pathlib import Path
import zipfile

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.attachment_parser import parse_attachment_text


def test_upload_text_attachment_and_bind_to_task_context(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            agent_file=tmp_path / "agents.json",
            workflow_file=tmp_path / "workflows.json",
            attachment_file=tmp_path / "attachments.json",
        )
    )

    uploaded = client.post(
        "/api/v1/task-attachments",
        files={"file": ("需求说明.txt", "第一行需求\n第二行约束".encode("utf-8"), "text/plain")},
    )

    assert uploaded.status_code == 201
    attachment = uploaded.json()
    assert attachment["filename"] == "需求说明.txt"
    assert attachment["extension"] == ".txt"
    assert attachment["status"] == "parsed"
    assert "第一行需求" in attachment["text_preview"]

    created = client.post(
        "/api/v1/tasks/requests",
        json={
            "source_type": "business_system",
            "title": "处理上传文档",
            "content": "请根据上传文档整理任务",
            "attachment_ids": [attachment["id"]],
        },
    )

    assert created.status_code == 201
    task = created.json()["tasks"][0]
    assert task["request_metadata"]["attachment_ids"] == [attachment["id"]]
    assert task["request_metadata"]["attachments"][0]["filename"] == "需求说明.txt"
    assert "attachment_text" not in task["request_metadata"]
    assert "第一行需求" in task["context"]["summary"]
    assert "需求说明.txt" in task["context"]["artifacts"][0]


def test_parse_docx_attachment_extracts_paragraphs_and_tables() -> None:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
              <w:body>
                <w:p><w:r><w:t>需求背景：客户需要自动化报告</w:t></w:r></w:p>
                <w:tbl><w:tr>
                  <w:tc><w:p><w:r><w:t>字段</w:t></w:r></w:p></w:tc>
                  <w:tc><w:p><w:r><w:t>说明</w:t></w:r></w:p></w:tc>
                </w:tr></w:tbl>
              </w:body>
            </w:document>""",
        )

    parsed = parse_attachment_text("需求文档.docx", buffer.getvalue())

    assert "需求背景：客户需要自动化报告" in parsed.text
    assert "字段" in parsed.text
    assert "说明" in parsed.text


def test_parse_xlsx_attachment_extracts_sheet_and_cell_text() -> None:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <sheets><sheet name="需求清单" sheetId="1" r:id="rId1" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/></sheets>
            </workbook>""",
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <si><t>模块</t></si>
              <si><t>需求</t></si>
              <si><t>任务发布</t></si>
              <si><t>支持上传文本附件</t></si>
            </sst>""",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <sheetData>
                <row><c t="s"><v>0</v></c><c t="s"><v>1</v></c></row>
                <row><c t="s"><v>2</v></c><c t="s"><v>3</v></c></row>
              </sheetData>
            </worksheet>""",
        )

    parsed = parse_attachment_text("需求清单.xlsx", buffer.getvalue())

    assert "Sheet: 需求清单" in parsed.text
    assert "模块 | 需求" in parsed.text
    assert "任务发布 | 支持上传文本附件" in parsed.text
