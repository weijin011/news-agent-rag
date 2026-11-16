from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from schemas.base import NewsItemBase

#检查收藏状态
class FavoriteCheckReponse(BaseModel):
    is_favorite:bool=Field(...,alias="isFavorite")

#添加收藏请求
class FavoriteAddRequest(BaseModel):
    news_id:int=Field(...,alias="newsId")

#规划两个类:一个是新闻模型类+收藏的模型类
class FavoriteNewsItemReponse(NewsItemBase):
    favorite_id:int=Field(...,alias="favoriteId")
    favorite_time:datetime=Field(...,alias="favoriteTime")
    model_config=ConfigDict(
        populate_by_name=True,#alias/字段名兼容
        from_attributes=True
    )

#收藏列表接口响应模型类
class FavoriteListResponse(BaseModel):
    list:list[FavoriteNewsItemReponse]
    total:int
    has_more:bool=Field(...,alias="hasMore")

    model_config=ConfigDict(
            populate_by_name=True,#alias/字段名兼容
            from_attributes=True
        )