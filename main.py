from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging
from config import settings
from database import engine, Base
from routers import audit_router, supervisor_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("正在初始化数据库...")
    Base.metadata.create_all(bind=engine)
    logger.info("数据库初始化完成")
    yield
    logger.info("服务正在关闭...")


app = FastAPI(
    title=settings.PROJECT_NAME,
    description="面向平台内容安全审核员的谣言扩散路径分析服务",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    logger.warning(f"参数验证错误: {str(exc)}")
    return JSONResponse(
        status_code=400,
        content={"detail": str(exc), "code": "VALIDATION_ERROR"}
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error(f"未处理的异常: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误，请稍后重试", "code": "INTERNAL_ERROR"}
    )


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": settings.PROJECT_NAME,
        "version": "1.0.0"
    }


@app.get("/")
async def root():
    return {
        "message": "谣言扩散分析服务 API",
        "docs": "/docs",
        "health": "/health"
    }


app.include_router(audit_router, prefix=settings.API_V1_PREFIX)
app.include_router(supervisor_router, prefix=settings.API_V1_PREFIX)
