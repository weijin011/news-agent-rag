from aiomysql import IntegrityError
from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from utils.exception import general_exception_handler, http_exception_handler, integrity_error_handler, sqlalchemy_error_handler



def register_exception_handlers(app):
    """
    注册全局异常处理，子类在前，父类在后，具体在前，抽象在后
    """
    app.add_exception_handler(HTTPException, http_exception_handler) #业务层面
    app.add_exception_handler(IntegrityError,integrity_error_handler) #数据完整性层面
    app.add_exception_handler(SQLAlchemyError,sqlalchemy_error_handler) #数据库层面
    app.add_exception_handler(Exception,general_exception_handler)#默认异常层面