from src.db.session import SessionLocal
from src.db.models import StreamLog
from datetime import datetime


def log_to_db(task_id: str, content: str, level: str = "INFO"):
    """
    将日志直接写入数据库，供前端实时轮询。
    同时打印到控制台，供后端调试。
    """
    # 1. 打印到后端控制台 (带时间戳)
    time_str = datetime.now().strftime("%H:%M:%S")
    print(f"[{time_str}] [{level}] Task[{task_id[:8]}]: {content}")

    # 2. 写入数据库
    db = SessionLocal()
    try:
        safe_content = content.encode('utf-8', 'replace').decode('utf-8')
        log = StreamLog(
            task_id=task_id,
            content=safe_content,
            level=level
        )
        db.add(log)
        db.commit()
    except Exception as e:
        print(f"❌ Log Error: {e}")
    finally:
        db.close()