"""
数据库 <-> CSV 文件 双向导入导出（极简版）
=======================================
只满足课程设计要求的一件事：
    · export_table(db, table)        —— 把一张表的数据写成 csv 文件（数据库 → 文件）
    · import_rows(db, table, rows)    —— 把 csv 里的行插入数据库（文件 → 数据库，主键重复则跳过）

本项目 models/ 下每个文件各自定义了自己的 Base（DeclarativeBase），没有一个统一的
metadata 能拿到全部表，所以这里用一份 TABLES 字典把「表名 -> ORM 模型」映射起来。

说明：news 表字段只有 int / str / datetime 三种类型，因此转换逻辑只处理这三种，
不做无谓的通用类型分支。
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from models.favorite import Favorite
from models.history import History
from models.news import Category, News
from models.users import User

# 可导入导出的表：表名 -> ORM 模型类
TABLES: Dict[str, Any] = {
    "news_category": Category,
    "news": News,
    "user": User,
    "favorite": Favorite,
    "history": History,
}

# 导出文件落盘目录（自动创建）
DATA_DIR: Path = Path(__file__).resolve().parent.parent / "data_files"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# CSV 写出用 utf-8-sig（带 BOM），Excel 双击打开中文不乱码
CSV_ENCODING = "utf-8-sig"
# 读取时按此顺序尝试解码，兼容 Excel 导出的 GBK 文件
CSV_READ_ENCODINGS = ("utf-8-sig", "utf-8", "gbk", "gb18030")
# news 表产生的标准时间字符串格式；导入时也只认这一种（含 ISO 兜底）
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


# ---------------------------------------------------------------------------
# 模型结构读取
# ---------------------------------------------------------------------------

def get_model(table: str) -> Any:
    """按表名取 ORM 模型；表名不支持时抛 ValueError"""
    model = TABLES.get(table)
    if model is None:
        raise ValueError(f"不支持的数据表：{table}，可选值为：{', '.join(TABLES)}")
    return model


def get_columns(model: Any) -> List[Any]:
    return list(model.__table__.columns)


def get_column_names(model: Any) -> List[str]:
    return [c.name for c in get_columns(model)]


def get_column_map(model: Any) -> Dict[str, Any]:
    return {c.name: c for c in get_columns(model)}


def get_primary_key_names(model: Any) -> List[str]:
    return [c.name for c in model.__table__.primary_key.columns]


# ---------------------------------------------------------------------------
# 文件里的原始字符串 -> ORM 需要的 Python 类型（导入方向）
# ---------------------------------------------------------------------------

def coerce_value(column: Any, raw: Any) -> Any:
    """按列的真实类型转换单个值。news 表只用得到 int / datetime / str 三种。"""
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return None

    python_type = column.type.python_type

    if python_type is int:
        return int(float(str(raw).strip()))            # category_id / views
    if python_type is datetime:
        text = str(raw).strip()
        if text.endswith("Z"):
            text = text[:-1]
        try:
            return datetime.strptime(text, DATETIME_FORMAT)
        except ValueError:
            return datetime.fromisoformat(text)         # 兜底兼容 ISO 格式
    return str(raw).strip()                             # 其余全部按字符串


def row_to_kwargs(model: Any, row: Dict[str, Any]) -> tuple:
    """把文件里的一行转成 Model(**kwargs) 可用的字典，返回 (kwargs, errors)"""
    column_map = get_column_map(model)
    kwargs: Dict[str, Any] = {}
    errors: List[str] = []
    for key, raw in (row or {}).items():
        if key is None:
            continue
        name = str(key).strip()
        if name not in column_map:
            continue
        try:
            kwargs[name] = coerce_value(column_map[name], raw)
        except ValueError as exc:
            errors.append(f"字段「{name}」{exc}")
    return kwargs, errors


# ---------------------------------------------------------------------------
# 写 CSV 文件（数据库 -> 文件）
# ---------------------------------------------------------------------------

def build_filename(prefix: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{stamp}.csv"


def write_csv(path: Path, rows: Sequence[Dict[str, Any]], headers: Sequence[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding=CSV_ENCODING, newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(headers), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in headers})
    return path


# ---------------------------------------------------------------------------
# 读 CSV 文件（文件 -> 行列表）
# ---------------------------------------------------------------------------

def parse_csv_bytes(content: bytes) -> List[Dict[str, Any]]:
    """解析 CSV：自动尝试多种编码，返回行列表（每行一个 dict）"""
    text = None
    for enc in CSV_READ_ENCODINGS:
        try:
            text = content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("CSV 文件编码无法识别，请另存为 UTF-8 或 GBK 编码")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV 文件没有表头行")
    rows: List[Dict[str, Any]] = []
    for raw in reader:
        row = {(k or "").strip(): v for k, v in raw.items() if k is not None}
        if any(v not in (None, "") for v in row.values()):
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# 数据库读写
# ---------------------------------------------------------------------------

async def fetch_rows(db: AsyncSession, model: Any) -> List[Dict[str, Any]]:
    """读出一张表的全部记录（按主键升序），转成字典列表（值直接由 csv 模块转字符串）"""
    pk_name = get_primary_key_names(model)[0]
    result = await db.execute(select(model).order_by(getattr(model, pk_name)))
    cols = get_column_names(model)
    return [{c: getattr(obj, c) for c in cols} for obj in result.scalars().all()]


async def export_table(db: AsyncSession, table: str) -> tuple:
    """数据库 → 文件：把一张表全部数据写成 csv，返回 (文件路径, 记录数)"""
    model = get_model(table)
    rows = await fetch_rows(db, model)
    path = DATA_DIR / build_filename(table)
    write_csv(path, rows, get_column_names(model))
    return path, len(rows)


async def import_rows(db: AsyncSession, table: str, rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """
    文件 → 数据库：把一批行插入指定表。
    · 主键已存在则跳过（不覆盖）；
    · 某一行出错只记录错误，不影响其它行；
    · 返回统计字典（total/created/skipped/failed/errors）。
    """
    model = get_model(table)
    pk_name = get_primary_key_names(model)[0]
    report = {"total": len(rows), "created": 0, "skipped": 0, "failed": 0, "errors": []}

    res = await db.execute(select(getattr(model, pk_name)))
    existing = set(res.scalars().all())

    for row_no, raw in enumerate(rows, start=1):
        if not isinstance(raw, dict):
            report["failed"] += 1
            report["errors"].append({"row": row_no, "reason": "该行不是键值对结构，已跳过"})
            continue

        kwargs, errors = row_to_kwargs(model, raw)
        if errors:
            report["failed"] += 1
            report["errors"].append({"row": row_no, "reason": "；".join(errors)})
            continue

        pk_value = kwargs.get(pk_name)
        if pk_value is not None and pk_value in existing:
            report["skipped"] += 1
            continue

        try:
            db.add(model(**kwargs))
            await db.flush()
            if pk_value is not None:
                existing.add(pk_value)
            report["created"] += 1
        except (IntegrityError, SQLAlchemyError) as exc:
            report["failed"] += 1
            report["errors"].append({"row": row_no, "reason": str(exc)})

    await db.commit()
    return report
