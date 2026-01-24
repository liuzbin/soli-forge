import os
from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    PROJECT_NAME: str = "Solidity Security Agent"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api"

    # 路径配置
    BASE_DIR: Path = Path(__file__).resolve().parent.parent.parent
    STORAGE_DIR: Path = BASE_DIR / "storage"

    # 数据库 (默认 SQLite，生产可用 PostgreSQL)
    DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR}/sql_app.db")

    # 安全配置 (请在 .env 中修改 SECRET_KEY)
    SECRET_KEY: str = os.getenv("SECRET_KEY", "CHANGE_THIS_TO_A_SUPER_SECRET_KEY")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # Token 7天过期

    # LLM 配置
    DASHSCOPE_API_KEY: str = os.getenv("DASHSCOPE_API_KEY", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "qwen-plus")

    class Config:
        env_file = ".env"


settings = Settings()

# 自动创建存储目录结构
os.makedirs(settings.STORAGE_DIR / "tasks", exist_ok=True)