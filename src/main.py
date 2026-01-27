import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.api.routes import tasks, auth
from src.core.config import settings
from src.db.session import engine
from src.db.base import Base

# 创建数据库表 (如果表不存在)
# 注意：在生产环境中通常使用 Alembic 做迁移，但开发环境这样最快
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json"
)

# CORS 配置 (允许前端访问)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["tasks"])

if __name__ == "__main__":
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        access_log=False,  # 关闭 "GET /... 200 OK" 这种刷屏日志
        log_level="warning"  # 只显示警告和错误，以及我们自己 print 的内容
    )
