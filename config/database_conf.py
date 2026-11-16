from sqlalchemy.ext.asyncio import AsyncSession,async_sessionmaker,create_async_engine

from config.settings import settings

#数据库URL：从 .env 读取，换库不需要动代码
ASYNC_DATABASE_URL=settings.database_url

#创建异步引擎
async_engine=create_async_engine(
    ASYNC_DATABASE_URL,
    echo=settings.database_echo,#开发环境可在 .env 里打开 SQL 日志
    pool_size=10,#可选，连接池大小
    max_overflow=20,#可选，超出连接池大小后，多余的连接数

)

#创建异步会话工厂
AsyncSessionLocal=async_sessionmaker(
    bind=async_engine,#绑定异步引擎
    class_=AsyncSession,#指定会话类
    expire_on_commit=False,#设置会话结束后不关闭连接，不需要重新查询数据库
)

#依赖项,用于获取数据库会话
async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session#返回数据库会话给路由处理函数
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()