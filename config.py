from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    API_V1_PREFIX: str = "/api/v1"
    PROJECT_NAME: str = "谣言扩散分析服务"
    DATABASE_URL: str = "sqlite:///./rumor_analysis.db"

    RISK_LEVELS: List[str] = ["低", "中", "高", "极高"]
    CATEGORIES: List[str] = ["公共安全", "民生政策", "医疗健康", "财经金融", "教育文化", "其他"]

    DIFFUSION_CHANNELS: List[str] = [
        "社交媒体群组", "短视频平台", "即时通讯私聊", "论坛社区", "自媒体账号", "其他"
    ]

    HIGH_RISK_THRESHOLD: int = 70
    MEDIUM_RISK_THRESHOLD: int = 40

    class Config:
        case_sensitive = True


settings = Settings()
