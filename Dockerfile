# 新闻资讯系统 + RAG/Agent 服务镜像
# 构建：docker build -t news-rag:latest .
# 运行：docker run -p 8000:8000 --env-file .env news-rag:latest
FROM python:3.13-slim

WORKDIR /app

# 先装依赖（利用 layer 缓存，改代码不用重装）
COPY requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt

# 再拷代码；.dockerignore 会排除 .venv / .chroma / node_modules / dist
COPY . .

# 向量索引如果不存在，容器启动后手动跑一次建库：
#   docker exec -it <容器> python -m scripts.build_index --source file --reset
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
