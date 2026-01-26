import os
import shutil
import uuid
import traceback
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from src.db.session import get_db
from src.db.models import Task, TestCase, StreamLog, User
from src.engine.manager import TaskManager
from src.engine.tools.file_manager import FileManager
from src.api.deps import get_current_user

router = APIRouter()


# 1. 获取任务列表
@router.get("/")
def list_tasks(
        page: int = 1,
        page_size: int = 20,
        owner_id: Optional[int] = None,
        keyword: Optional[str] = None,
        status: Optional[str] = None,
        creator_name: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    query = db.query(Task).filter(Task.is_deleted == False)

    if owner_id:
        query = query.filter(Task.owner_id == owner_id)
    if keyword:
        query = query.filter(Task.name.like(f"%{keyword}%"))
    if status and status != "all":
        query = query.filter(Task.status == status)
    if creator_name:
        query = query.join(User).filter(User.username.like(f"%{creator_name}%"))
    if start_date:
        query = query.filter(Task.created_at >= start_date)
    if end_date:
        query = query.filter(Task.created_at <= end_date + " 23:59:59")

    total = query.count()
    tasks = query.order_by(Task.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()

    items = []
    for t in tasks:
        items.append({
            "id": t.id,
            "name": t.name,
            "status": t.status,
            "contract_name": t.contract_name,
            "created_at": t.created_at,
            "started_at": t.started_at,
            "duration": t.duration,
            "owner_name": t.owner.username if t.owner else "Unknown"
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size
    }


# 2. 创建任务
@router.post("/create")
def create_task(
        name: str,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    task_id = str(uuid.uuid4())
    new_task = Task(
        id=task_id,
        name=name,
        status="created",
        owner_id=current_user.id
    )
    db.add(new_task)
    db.commit()
    db.refresh(new_task)
    return new_task


# 3. 上传合约 (👉 修复点：路径对齐)
@router.post("/{task_id}/upload")
async def upload_contract(
        task_id: str,
        file: UploadFile = File(...),
        db: Session = Depends(get_db)
):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    try:
        # 使用 FileManager 获取正确的任务目录: storage/tasks/{id}
        fm = FileManager(db, task_id)

        # 确保目录存在
        if not fm.task_dir.exists():
            fm.task_dir.mkdir(parents=True, exist_ok=True)

        # 直接使用文件名保存，不要加前缀，否则 FileManager 找不到
        file_path = fm.task_dir / file.filename
        print(f"DEBUG: Uploading file to {file_path}")

        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        file.file.seek(0)
        content = await file.read()

        try:
            source_code = content.decode("utf-8")
        except UnicodeDecodeError:
            source_code = "// Error: Unable to decode file content."

        task.contract_name = file.filename
        task.source_code = source_code
        task.codes = {"original": source_code, "fix": ""}
        task.status = "uploaded"

        db.commit()
        return {"status": "success", "filename": file.filename}

    except Exception as e:
        print(f"❌ Upload Error: {str(e)}")
        traceback.print_exc()
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


# 4. 启动任务
@router.post("/{task_id}/start")
def start_task(task_id: str, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    try:
        manager = TaskManager(db, task_id)
        manager.start_execution()
        return {"status": "started"}
    except Exception as e:
        print(f"Start Error: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# 5. 停止任务
@router.post("/{task_id}/stop")
def stop_task(task_id: str, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if task:
        task.status = "stopped"
        db.commit()
    return {"status": "stopped"}


# 6. 获取详情
@router.get("/{task_id}/detail")
def get_task_detail(task_id: str, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return {
        "id": task.id,
        "name": task.name,
        "status": task.status,
        "contract_name": task.contract_name,
        "codes": {
            "original": task.source_code,
            "fix": task.fixed_code
        },
        "slither_report": task.slither_report,
        "matrix_cases": task.test_cases,
        "created_at": task.created_at,
        "started_at": task.started_at,
        "duration": task.duration
    }


# 7. 获取日志
@router.get("/{task_id}/logs")
def get_logs(task_id: str, db: Session = Depends(get_db)):
    logs = db.query(StreamLog) \
        .filter(StreamLog.task_id == task_id) \
        .order_by(StreamLog.timestamp.asc()) \
        .all()

    return [{"time": log.timestamp, "level": log.level, "content": log.content} for log in logs]


# 8. 删除任务
@router.delete("/{task_id}")
def delete_task(
        task_id: str,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    task.is_deleted = True
    db.commit()

    return {"status": "success", "id": task_id, "message": "Task moved to trash"}