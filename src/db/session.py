from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.core.config import settings

# 创建数据库引擎
# check_same_thread=False 仅用于 SQLite，如果你用 MySQL 可以去掉 connect_args
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    # connect_args={"check_same_thread": False} # 如果是 SQLite 请取消注释
)

# 创建会话工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# Dependency (FastAPI 用它来获取数据库连接)
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()