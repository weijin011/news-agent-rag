import asyncio
import json
import time

import redis.asyncio as redis

from config.settings import settings
from utils.logging_conf import get_logger

logger = get_logger(__name__)

REDIS_HOST = settings.redis_host
REDIS_PORT = settings.redis_port
REDIS_DB = settings.redis_db
REDIS_TIMEOUT = settings.redis_timeout  # 秒：Redis 不可达时，单次操作最多等待时间

#创建Redis的连接对象
redis_client = redis.Redis(
    host=REDIS_HOST,  # Redis服务器的主机地址
    port=REDIS_PORT,  # Redis端口号
    db=REDIS_DB,  # Redis数据库编号,0-15
    decode_responses=True,  # 是否将字节数据解码为字符串
    protocol=2,  # 兼容 Redis 5.x，禁用 HELLO 握手
)

# Redis 临时禁用时间戳：非 0 时表示到该时刻前不再尝试连接（避免每次请求都傻等）
_redis_disabled_until = 0


async def _safe(coro, default=None):
    """带超时的安全执行：Redis 不可达时在 REDIS_TIMEOUT 秒内失败并降级，绝不阻塞接口。"""
    global _redis_disabled_until
    now = time.time()
    if _redis_disabled_until and now < _redis_disabled_until:
        return default
    try:
        return await asyncio.wait_for(coro, timeout=REDIS_TIMEOUT)
    except Exception as e:
        logger.warning("Redis 暂不可用，已降级: %s", e)
        _redis_disabled_until = now + 60  # 60 秒内不再尝试，避免每次请求都等待
        return default


#设置和读取（字符串和列表或字典） "[]""{}"
#读取:字符串
async def get_cache(key: str):
    return await _safe(redis_client.get(key))


#读取:列表或字典
async def get_json_cache(key: str):
    data = await _safe(redis_client.get(key))
    if data:
        try:
            return json.loads(data)  # 反序列化
        except Exception:
            return None
    return None


#设置缓存setex(key,expire,value)
async def set_cache(key: str, value: str, expire: int = None):
    try:
        if isinstance(value, (dict, list)):
            # 转字符串再存
            value = json.dumps(value, ensure_ascii=False)  # 中文正常保存
        return await _safe(redis_client.setex(key, expire, value), default=False)
    except Exception as e:
        logger.warning("设置缓存失败: %s", e)
        return False