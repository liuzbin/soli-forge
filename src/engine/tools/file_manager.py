import shutil
from pathlib import Path
from fastapi import UploadFile
from src.core.config import settings
from src.db.models import Task  # 👈 引入 Task 模型


class FileManager:
    def __init__(self, db, task_id: str):
        """
        初始化文件管理器
        :param db: 数据库会话
        :param task_id: 任务 UUID
        """
        self.db = db
        self.task_id = task_id

        # 任务根目录: storage/tasks/{task_id}/
        self.task_dir = settings.BASE_DIR / "storage" / "tasks" / task_id

        # 原始合约存放目录
        self.original_dir = self.task_dir

        # 确保目录存在
        if not self.task_dir.exists():
            self.task_dir.mkdir(parents=True, exist_ok=True)

    @property
    def task(self):
        """
        👈 新增：通过 helper 属性获取 Task 对象
        这样 fm.task.contract_name 就能正常工作了
        """
        return self.db.query(Task).filter(Task.id == self.task_id).first()

    def save_original_file(self, file: UploadFile) -> Path:
        """
        保存前端上传的原始合约文件
        """
        file_path = self.task_dir / file.filename

        # 写入磁盘
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        return file_path

    def save_artifact(self, filename: str, content: str, artifact_type: str = "unknown") -> Path:
        """
        保存生成的产物 (如 exploit.sol, report.json 等)
        """
        file_path = self.task_dir / filename

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

        return file_path

    def update_current_source(self, content: str):
        """
        更新当前轮次的代码状态
        """
        self.save_artifact("latest.sol", content, "source")

    @property
    def settings(self):
        return settings
