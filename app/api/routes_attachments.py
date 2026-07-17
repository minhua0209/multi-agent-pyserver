from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status

from app.api.auth import current_user
from app.core.models import TaskAttachment, new_id, utc_now
from app.services.attachment_parser import ALLOWED_TEXT_ATTACHMENT_EXTENSIONS, parse_attachment_text

router = APIRouter(prefix="/api/v1/task-attachments", tags=["task-attachments"])

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
TEXT_PREVIEW_CHARS = 2_000


@router.post("", response_model=TaskAttachment, status_code=status.HTTP_201_CREATED)
async def upload_task_attachment(request: Request, file: UploadFile = File(...)) -> TaskAttachment:
    user = current_user(request)
    filename = Path(file.filename or "").name
    extension = Path(filename).suffix.lower()
    if not filename or extension not in ALLOWED_TEXT_ATTACHMENT_EXTENSIONS:
        raise HTTPException(status_code=400, detail="仅支持 .docx、.xlsx、.txt、.md、.log 文本附件")

    data = await file.read(MAX_ATTACHMENT_BYTES + 1)
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=413, detail="附件不能超过 10MB")

    try:
        parsed = parse_attachment_text(filename, data)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"附件解析失败：{exc}") from exc

    now = utc_now()
    attachment_id = new_id("att")
    stored_filename = f"{attachment_id}{extension}"
    attachment = TaskAttachment(
        id=attachment_id,
        filename=filename,
        stored_filename=stored_filename,
        content_type=file.content_type or "",
        extension=extension,
        size_bytes=len(data),
        text_preview=parsed.text[:TEXT_PREVIEW_CHARS],
        text_content=parsed.text,
        text_length=len(parsed.text),
        truncated=parsed.truncated,
        status="parsed",
        created_by_user_id=user.id,
        created_by_user_name=user.name,
        created_at=now,
        updated_at=now,
    )
    request.app.state.attachment_store.save_file(stored_filename, data)
    return request.app.state.attachment_store.save(attachment)
