from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import ai, data_transfer, favorite, history, news, session, users
from utils.exception_handlers import register_exception_handlers
from utils.logging_conf import setup_logging
from utils.middleware import request_context_middleware

# 尽早初始化日志，保证后续 import 阶段的告警也能被格式化输出
setup_logging()

app=FastAPI(
    title="新闻资讯系统",
    description="FastAPI + MySQL + Redis 实现的新闻资讯服务，"
                "支持数据库与文件之间的双向导入导出",
    version="1.0.0",
)

#注册异常处理器
register_exception_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],#允许的源，开发阶段允许所有源
    allow_credentials=True,#允许携带cookie
    allow_methods=["*"],#允许的请求方法
    allow_headers=["*"],#允许的请求头
)

# 请求上下文：为每个请求生成 request_id 并注入日志
app.middleware("http")(request_context_middleware)


#挂载路由
app.include_router(news.router)

#挂载用户模块路由
app.include_router(users.router)

#收藏新闻的路由
app.include_router(favorite.router)

#游览历史的路由
app.include_router(history.router)

#数据导入导出的路由（新增，仅满足数据库↔文件互导需求）
app.include_router(data_transfer.router)

#AI 问答：模型出口统一收归后端，前端不再持有密钥
app.include_router(ai.router)

#会话管理：多轮对话持久化（MySQL 不可用时自动降级为文件存储）
app.include_router(session.router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app="main:app", host="127.0.0.1", port=8000, reload=True)
