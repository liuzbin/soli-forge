from src.db.session import SessionLocal
from src.db.models import StreamLog


def log_to_db(task_id: str, content: str, level: str = "INFO"):
    """
    将日志直接写入数据库，供前端实时轮询
    """
    db = SessionLocal()
    try:
        # 去掉可能导致编码问题的特殊字符
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