"""
数据导入 / 导出路由（极简版，仅 2 个接口）
=========================================
统一前缀：/api/data

    GET  /api/data/export   把数据库里的一张表导出成 csv 文件并下载
    POST /api/data/import   上传 csv 文件，把数据插入数据库

GUI/网站要求已由原项目前端 + FastAPI 自带的 /docs 交互页面满足，这里不另做界面。
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from config.database_conf import get_db
from utils import data_transfer
from utils.response import success_response

router = APIRouter(prefix="/api/data", tags=["数据导入导出"])

MULTIPART_HINT = (
    "文件导入功能需要 python-multipart 库，请在虚拟环境中执行 "
    "pip install python-multipart 并重启服务后使用。"
)


def _bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)


# --------------------------------------------------------------------------
# 导出：数据库 → 文件
# --------------------------------------------------------------------------

@router.get("/export", summary="导出一张表为 CSV 文件并下载")
async def export_table(
    table: str = Query(..., description="表名，如 news、user"),
    db: AsyncSession = Depends(get_db),
):
    """把指定表的全部数据写成 csv 文件并返回下载。文件同时保存在 data_files/ 目录。"""
    try:
        path, count = await data_transfer.export_table(db, table)
    except ValueError as exc:
        raise _bad_request(str(exc))
    return FileResponse(path=path, filename=path.name)


# --------------------------------------------------------------------------
# 导入：文件 → 数据库
# --------------------------------------------------------------------------

async def import_from_file(
    file: UploadFile = File(..., description="要导入的 .csv 文件"),
    table: str = Query(..., description="要导入的表名，如 news、user"),
    db: AsyncSession = Depends(get_db),
):
    """把上传的 csv 文件里的数据插入指定表（主键重复的行自动跳过）。"""
    content = await file.read()
    if not content:
        raise _bad_request("上传的文件是空的")

    name = (file.filename or "").lower()
    try:
        if name.endswith(".csv"):
            rows = data_transfer.parse_csv_bytes(content)
        else:
            raise ValueError("仅支持 .csv 文件")
    except ValueError as exc:
        raise _bad_request(str(exc))

    try:
        data_transfer.get_model(table)  # 先校验表名是否合法
    except ValueError as exc:
        raise _bad_request(str(exc))

    report = await data_transfer.import_rows(db, table, rows)
    return success_response(message="导入完成", data=report)


async def import_unavailable():
    """python-multipart 缺失时的占位接口：返回安装指引而不是 404 / 启动失败"""
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=MULTIPART_HINT)


# 条件注册上传接口：UploadFile / File 在路由定义阶段就会检查 python-multipart，
# 缺库时若直接写装饰器会让整个应用启动失败；故改为条件注册，缺库只影响这一个接口。
try:
    import multipart  # python-multipart 的导入名是 multipart

    router.post("/import", summary="上传 CSV 文件导入数据库")(import_from_file)
except ImportError:
    router.post("/import", summary="上传文件导入数据库（缺少依赖，暂不可用）")(import_unavailable)
