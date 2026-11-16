

from crud.history import clear_history
from schemas.history import HistoryNewsItemReponse
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from config.database_conf import get_db
from crud import history
from models.users import User
from schemas.history import HistoryAddRequest
from utils.auth import get_current_user
from utils.response import success_response
from schemas.history import HistoryListResponse

router=APIRouter(prefix="/api/history",tags=["history"])

@router.post("/add")
async def add_history(data: HistoryAddRequest,
                      user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_db)):
    """
    添加历史记录
    """
    result = await history.add_history(db, user.id, data.news_id)
    return success_response(message="添加成功", data=result)

#获取浏览历史列表
@router.get("/list")
async def get_history_list(
    page: int = Query(1, ge=1),
    page_size: int=Query(10, ge=1, le=100, alias="pageSize"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    rows, total = await history.get_history_list(db, user.id, page, page_size)
    has_more = total > page * page_size
    history_list = [HistoryNewsItemReponse.model_validate({
        **news.__dict__,
        "view_time": view_time,
        "history_id": history_id
    }) for news, view_time, history_id in rows]
    data = HistoryListResponse(list=history_list, total=total, hasMore=has_more)
    return success_response(message="获取历史列表成功",data=data)

@router.delete("/delete/{history_id}")
async def delete_history(history_id: int,
                       user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_db)):
    """
    删除历史记录
    """
    if not history.delete_history(db, user.id, history_id):
        raise HTTPException(status_code=404,detail="浏览记录不存在")
    return success_response(message="删除成功")


#清空给浏览记录
@router.delete("/clear")
async def clear_history_list(user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_db)):
        result=await clear_history(db,user.id)
        return success_response(message=f"清空{result}条浏览记录成功")