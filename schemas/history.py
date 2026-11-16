from datetime import datetime
 
from pydantic import BaseModel, Field, ConfigDict
 
from schemas.base import NewsItemBase
 
 
class HistoryAddRequest(BaseModel):
    """
    添加历史记录请求
    """
    news_id: int = Field(..., alias="newsId")

class HistoryNewsItemReponse(NewsItemBase):
    """
    浏览历史列表中的新闻项响应
    """
    history_id: int = Field(alias="historyId")
    view_time: datetime = Field(..., alias="viewTime")
    model_config=ConfigDict(
            populate_by_name=True,#alias/字段名兼容
            from_attributes=True
        )


class HistoryListResponse(BaseModel):
    """
    收藏列表响应
    """
    list:list[HistoryNewsItemReponse]
    total:int
    has_more:bool=Field(...,alias="hasMore")
    model_config=ConfigDict(
            populate_by_name=True,#alias/字段名兼容
            from_attributes=True
        )