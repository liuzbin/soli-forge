# Web 后端运行环境
FROM python:3.10-slim

WORKDIR /app

# 安装 Docker CLI (为了让后端能执行 'docker run ...')
RUN apt-get update && apt-get install -y docker.io

# 复制依赖配置
COPY requirements.txt .

# 安装 Python 依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制源代码
COPY src ./src
COPY .env .

# 创建存储目录
RUN mkdir -p storage/tasks

# 暴露端口
EXPOSE 8000

# 启动命令
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]