"""切块：纯标准库实现，不依赖任何 AI 服务，方便单独测试。

两个关键决策：
1. 按句边界切，不按字符硬切 —— 硬切会把"因为…所以…"截断，
   向量化后这一段就变成了噪音。
2. 重叠也按句回退 —— 保证相邻块共享完整句子，而不是半个词。
"""
import re
from typing import List

# 句末标点 + 换行；用零宽后顾断言保留标点本身
_SENT_BOUNDARY = re.compile(r"(?<=[。！？!?；;\n])")


def split_sentences(text: str) -> List[str]:
    if not text or not text.strip():
        return []
    parts = _SENT_BOUNDARY.split(text)
    return [p.strip() for p in parts if p and p.strip()]


def _tail_with_overlap(sentences: List[str], overlap: int) -> List[str]:
    """从上一块尾部取若干整句作为重叠，总长度不超过 overlap。"""
    if overlap <= 0 or not sentences:
        return []
    kept: List[str] = []
    used = 0
    for s in reversed(sentences):
        if used + len(s) > overlap:
            break
        kept.insert(0, s)
        used += len(s)
    return kept


def chunk_text(text: str, size: int = 500, overlap: int = 50) -> List[str]:
    """把长文本切成不超过 size 字符的片段，块间保留 overlap 字符的重叠。"""
    if size <= 0:
        raise ValueError("size 必须为正数")
    if overlap < 0 or overlap >= size:
        raise ValueError("overlap 必须满足 0 <= overlap < size")

    sentences = split_sentences(text)
    if not sentences:
        return []

    chunks: List[str] = []
    buf: List[str] = []
    buf_len = 0

    for sent in sentences:
        # 极少数无标点的超长段落：退化成硬切，避免把内容整体丢弃
        if len(sent) > size:
            if buf:
                chunks.append("".join(buf))
                buf, buf_len = [], 0
            for i in range(0, len(sent), size):
                piece = sent[i:i + size]
                if piece.strip():
                    chunks.append(piece)
            continue

        if buf and buf_len + len(sent) > size:
            chunks.append("".join(buf))
            buf = _tail_with_overlap(buf, overlap)
            buf_len = sum(len(s) for s in buf)
            # 重叠 + 当前句仍超限时丢掉重叠，防止产出大量重复块
            if buf_len + len(sent) > size:
                buf, buf_len = [], 0

        buf.append(sent)
        buf_len += len(sent)

    if buf:
        chunks.append("".join(buf))
    return [c for c in chunks if c.strip()]


def build_chunks(title: str, content: str, size: int = 500, overlap: int = 50) -> List[str]:
    """标题放在每个块的首行：短、信息密度高，实测能提升检索命中。

    注意：分类 / 发布时间 / news_id 绝不塞进正文，它们只作为 Chroma
    元数据用于结构化过滤。把结构化字段混进语义文本会污染向量。
    """
    prefix = f"【{title.strip()}】\n" if title and title.strip() else ""
    budget = max(size - len(prefix), 80)
    ov = min(overlap, budget - 1)
    return [prefix + c for c in chunk_text(content, size=budget, overlap=ov)]
