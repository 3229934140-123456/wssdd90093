from pydantic import BaseModel, Field, model_validator
from datetime import datetime
from typing import List, Optional, Dict
from config import settings


class AuditQueryBase(BaseModel):
    text_content: Optional[str] = Field(None, description="疑似谣言文本内容")
    topic_tags: Optional[List[str]] = Field(None, description="话题标签列表")
    content_url: Optional[str] = Field(None, description="内容链接")
    ticket_id: str = Field(..., description="审核工单ID")
    submitter: str = Field(..., description="审核员账号")

    @model_validator(mode='after')
    def check_at_least_one(self):
        if not self.text_content and not self.topic_tags and not self.content_url:
            raise ValueError('必须提供文本内容、话题标签或内容链接中的至少一项')
        return self


class AuditQueryCreate(AuditQueryBase):
    pass


class SourceInfo(BaseModel):
    source_url: str
    source_type: str
    publish_time: datetime
    author: Optional[str] = None
    platform: str
    confidence: float = Field(..., ge=0, le=1)


class DiffusionChannel(BaseModel):
    channel_name: str
    share_count: int
    growth_rate: float
    region: Optional[str] = None
    is_rapid_growth: bool


class RiskAssessment(BaseModel):
    risk_level: str
    risk_score: int = Field(..., ge=0, le=100)
    category: str
    key_factors: List[str]


class ActionableTip(BaseModel):
    tip_type: str
    content: str
    severity: str
    suggestion: str


class DebunkInfo(BaseModel):
    exists: bool
    debunk_url: Optional[str] = None
    debunk_authority: Optional[str] = None
    coverage_ratio: Optional[float] = Field(None, ge=0, le=1)


class AnalysisResult(BaseModel):
    earliest_source: Optional[SourceInfo]
    main_channels: List[DiffusionChannel]
    risk_assessment: RiskAssessment
    actionable_tips: List[ActionableTip]
    debunk_info: DebunkInfo
    similar_cases_count: int
    analyzed_at: datetime


class AuditQueryResponse(BaseModel):
    query_id: int
    ticket_id: str
    status: str
    result: Optional[AnalysisResult] = None
    created_at: datetime
    completed_at: Optional[datetime] = None


class HighRiskRumor(BaseModel):
    rumor_id: int
    title: str
    category: str
    risk_level: str
    risk_score: int
    first_seen: datetime
    last_active: datetime
    total_shares: int
    affected_regions: List[str]
    debunk_status: str
    handle_status: str
    handle_stage: str = "待处置"
    main_channels: List[str] = []
    recent_growth_rate: Optional[float] = None
    suggested_action: Optional[str] = None


class CategoryGroup(BaseModel):
    category: str
    count: int
    rumors: List[HighRiskRumor]


class DailyHighRiskResponse(BaseModel):
    date: str
    total_high_risk: int
    by_category: Dict[str, int]
    rumors: List[HighRiskRumor]


class DailyHighRiskGroupedResponse(BaseModel):
    date: str
    total_high_risk: int
    by_category: Dict[str, int]
    groups: List[CategoryGroup]
    sort_by: str


class SpreadTrendItem(BaseModel):
    timestamp: datetime
    share_count: int
    after_intervention: bool


class InterventionStats(BaseModel):
    pre_avg_daily: Optional[float] = None
    post_new_shares: Optional[int] = None
    reduction_rate: Optional[float] = None
    pre_period_days: Optional[int] = None
    post_period_days: Optional[int] = None


class SpreadTrackResponse(BaseModel):
    rumor_id: int
    title: str
    intervention_time: Optional[datetime] = None
    trend: List[SpreadTrendItem]
    effect_evaluation: Optional[str] = None
    intervention_stats: Optional[InterventionStats] = None
    observation_status: str = "待观察"


class TipFeedbackCreate(BaseModel):
    query_id: int
    tip_type: str = Field(..., description="提示类型")
    feedback: str = Field(..., pattern="^(准确|不准确|已采用)$", description="反馈类型")
    submitter: str = Field(..., description="审核员账号")


class TipFeedbackResponse(BaseModel):
    id: int
    query_id: int
    tip_type: str
    feedback: str
    submitter: str
    created_at: datetime


class TipFeedbackSummary(BaseModel):
    tip_type: str
    total: int
    accurate: int
    inaccurate: int
    adopted: int
    adoption_rate: float = 0.0


class DailyTrendItem(BaseModel):
    date: str
    high_risk_count: int
    avg_risk_score: float
    total_shares: int


class CategoryTrendItem(BaseModel):
    category: str
    daily_trend: List[DailyTrendItem]
    trend_direction: str = "平稳"
    change_rate: Optional[float] = None


class CrossDayComparisonResponse(BaseModel):
    period_days: int
    start_date: str
    end_date: str
    overall: List[DailyTrendItem]
    by_category: List[CategoryTrendItem]


class StageSummaryItem(BaseModel):
    stage: str
    count: int
    avg_reduction_rate: Optional[float] = None


class CategoryStageItem(BaseModel):
    category: str
    count: int
    avg_risk_score: float


class HandleEffectSummary(BaseModel):
    date: str
    total_high_risk: int
    by_stage: List[StageSummaryItem]
    ineffective_by_category: List[CategoryStageItem]
    overall_avg_reduction: Optional[float] = None
    effective_rate: Optional[float] = None
