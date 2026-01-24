from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from src.db.session import get_db
from src.db.models import Task, TestCase, StreamLog
from src.engine.manager import TaskManager
import shutil
import uuid

router = APIRouter()


@router.post("/create")
def create_task(name: str = "Untitled Task", db: Session = Depends(get_db)):
    task_id = str(uuid.uuid4())
    new_task = Task(id=task_id, name=name, status="created")
    db.add(new_task)
    db.commit()
    db.refresh(new_task)
    return new_task


@router.post("/{task_id}/upload")
def upload_contract(task_id: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    tm = TaskManager(db, task_id)
    tm.file_manager.save_uploaded_file(file)

    task.contract_name = file.filename
    task.status = "uploaded"
    task.source_code = tm.file_manager.get_contract_content(file.filename)
    db.commit()

    return {"message": "File uploaded"}


@router.post("/{task_id}/start")
def start_task(task_id: str, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    tm = TaskManager(db, task_id)
    tm.start_execution()

    return {"message": "Task started"}


@router.post("/{task_id}/stop")
def stop_task(task_id: str, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    task.status = "stopped"
    db.commit()
    return {"message": "Task stopping..."}


@router.get("/{task_id}/detail")
def get_task_detail(task_id: str, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # 获取关联的 test_cases
    test_cases = db.query(TestCase).filter(TestCase.task_id == task_id).all()

    return {
        "id": task.id,
        "name": task.name,
        "status": task.status,
        "display_status": task.current_phase or task.status,
        "contract_name": task.contract_name,
        "slither_report": task.slither_report,
        "codes": {
            "original": task.source_code,
            "exploit": task.exploit_code,
            "fix": task.fixed_code
        },
        # 👇 新增字段：返回矩阵数据
        "matrix_cases": [
            {
                "id": tc.id,
                "name": tc.name,
                "source": tc.source,
                "status": tc.status,
                "description": tc.description,
                "code": tc.code
            }
            for tc in test_cases
        ]
    }


@router.get("/{task_id}/logs")
def get_task_logs(task_id: str, db: Session = Depends(get_db)):
    """
        ✅ 真实实现：从数据库查询实时日志
        """
    # 按时间正序排列
    logs = db.query(StreamLog) \
        .filter(StreamLog.task_id == task_id) \
        .order_by(StreamLog.timestamp) \
        .all()

    return [
        {
            # 转 ISO 格式字符串供前端解析
            "time": log.timestamp.isoformat() if log.timestamp else "",
            "level": log.level,
            "content": log.content
        }
        for log in logs
    ]


# ⚠️ 注意：日志部分保持你原有的逻辑，或者添加 Log 模型查询
# 之前的示例中我们用了 execution_xxx.log 文件，建议改为数据库查询
# 在此补充一个简易的日志查询实现：
@router.get("/{task_id}/logs")
def get_task_logs(task_id: str, db: Session = Depends(get_db)):
    # 假设你在 models.py 里加了 Log 表
    # return db.query(Log).filter(Log.task_id == task_id).order_by(Log.timestamp).all()

    # 或者读取文件日志 (之前的逻辑)
    import os
    log_file = f"execution_{task_id}.log"
    if os.path.exists(log_file):
        logs = []
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                # 简单解析
                parts = line.split("] ", 1)
                if len(parts) > 1:
                    logs.append({"time": "2024-01-01T00:00:00", "content": line.strip(), "level": "INFO"})
        return logs
    return []


@router.get("/")
def list_tasks(
        page: int = 1,
        page_size: int = 20,
        db: Session = Depends(get_db)
):
    """
    获取任务列表（支持分页）
    """
    # 计算偏移量
    offset = (page - 1) * page_size

    # 查询总数
    total = db.query(Task).count()

    # 查询当前页数据 (按创建时间倒序)
    tasks = db.query(Task) \
        .order_by(Task.created_at.desc()) \
        .offset(offset) \
        .limit(page_size) \
        .all()

    return {
        "items": tasks,
        "total": total,
        "page": page,
        "page_size": page_size
    }