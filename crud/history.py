from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, func, select

from models.news import News
from models.users import User
from models.history import History

#添加浏览记录
async def add_history(db: AsyncSession, user_id: int, news_id: int):
    """
    添加历史记录
    """
    query = select(History).where(History.user_id == user_id, History.news_id == news_id)
    result = await db.execute(query)
    existing_history = result.scalar_one_or_none()
    if existing_history:
        existing_history.view_time = datetime.now()
        await db.commit()
        await db.refresh(existing_history)
        return existing_history
    else:
        history = History(user_id=user_id, news_id=news_id)
        db.add(history)
        await db.commit()
        await db.refresh(history)
        return history

    #获取浏览历史列表
async def get_history_list(db: AsyncSession, user_id: int, page: int = 1, page_size: int = 10):
        #获取总量
        stmt=select(func.count((History.id))).where(History.user_id == user_id)
        result=await db.execute(stmt)
        total=result.scalar_one()

        offset=(page-1)*page_size
        query=(select(News,History.view_time.label("viewTime"),History.id.label("historyId"))
                .join(History,History.news_id==News.id)
                .where(History.user_id==user_id)
                .order_by(History.view_time.desc())
                .offset(offset).limit(page_size)
               )
        result= await db.execute(query)
        rows=result.all()
        return rows,total

#删除单条浏览记录
async def delete_history(db: AsyncSession, user_id: int, history_id: int):
    stmt=delete(History).where(History.id==history_id,History.user_id==user_id)
    result=await db.execute(stmt)
    await db.commit()
    return result.rowcount>0

#清空浏览记录
async def clear_history(db: AsyncSession, user_id: int):
    stmt=delete(History).where(History.user_id==user_id)
    result=await db.execute(stmt)
    await db.commit()
    return result.rowcount