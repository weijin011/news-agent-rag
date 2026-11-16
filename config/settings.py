"""集中式配置：所有可变参数从 .env 读取，代码里不再出现任何密钥/地址字面量。

用法：
    from config.settings import settings
    settings.ai_api_key

设计说明：
1. env_file 用绝对路径，避免"从别的目录启动 uvicorn / 跑 scripts 时读不到 .env"。
2. extra="ignore" 让 .env 里可以放前端或其他工具的变量而不报错。
3. 所有模型相关配置都走 OpenAI 兼容协议，换供应商只改 .env，不动代码。
"""
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# config/ 的上一级就是项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- 生成模型（DeepSeek，OpenAI 兼容） ----------
    ai_api_key: str = Field(default="", description="生成模型 API Key，仅存于服务端 .env")
    ai_base_url: str = "https://api.deepseek.com/v1"
    ai_chat_model: str = "deepseek-chat"

    # ---------- Embedding ----------
    # DeepSeek 官方 API 不提供 embeddings 接口，向量化继续走 DashScope；
    # 两套服务各自独立配置，换生成模型时不会影响已建好的向量索引。
    embedding_api_key: str = Field(default="", description="Embedding 服务 API Key")
    embedding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ai_embedding_model: str = "text-embedding-v3"

    ai_timeout: float = 30.0
    ai_max_retries: int = 2
    # 同时打向模型服务的最大并发数，防止 429
    ai_max_concurrency: int = Field(default=3, ge=1, le=20)

    # ---------- Embedding 批次 ----------
    # DashScope 兼容模式单次最多 10 条，别调大
    embedding_batch_size: int = 10

    # ---------- 切块与检索 ----------
    rag_chunk_size: int = Field(default=500, ge=100, le=2000)
    rag_chunk_overlap: int = Field(default=50, ge=0, le=200)
    rag_top_k: int = Field(default=5, ge=1, le=20)
    # cosine 相似度阈值：低于它的片段视为不相关，用于触发"库中没有相关信息"
    rag_score_threshold: float = Field(default=0.62, ge=0.0, le=1.0)

    # ---------- Chroma ----------
    chroma_persist_dir: str = str(PROJECT_ROOT / ".chroma")
    chroma_collection: str = "news_chunks"

    # ---------- 原有基础设施（从硬编码迁来） ----------
    database_url: str = "mysql+aiomysql://root:123456@localhost:3306/news_app?charset=utf8mb4"
    database_echo: bool = False
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_timeout: float = 2.0

    def require_ai_key(self) -> str:
        """需要真正调用模型时才校验，避免建库脚本之外的模块启动时就炸。"""
        if not self.ai_api_key:
            raise RuntimeError(
                "未配置 AI_API_KEY：请复制 .env.example 为 .env 并填入模型服务密钥"
            )
        return self.ai_api_key

    def require_embedding_key(self) -> str:
        """向量化单独校验；DeepSeek 不提供 embedding，必须配 DashScope 的 key。"""
        if not self.embedding_api_key:
            raise RuntimeError(
                "未配置 EMBEDDING_API_KEY：DeepSeek 无 embedding 接口，"
                "请在 .env 中填入 DashScope 密钥"
            )
        return self.embedding_api_key


settings = Settings()
