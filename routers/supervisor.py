from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import datetime, date, timedelta
from typing import List, Optional
from collections import defaultdict
from database import get_db
from schemas import (
    DailyHighRiskResponse, HighRiskRumor,
    SpreadTrackResponse, SpreadTrendItem
)
from models import RumorCase, SpreadRecord
from config import settings

router = APIRouter(prefix="/supervisor", tags=["主管视图"])


@router.get("/daily-high-risk", response_model=DailyHighRiskResponse)
def get_daily_high_risk(
    target_date: Optional[str] = None,
    category: Optional[str] = None,
    min_risk_score: int = Query(settings.HIGH_RISK_THRESHOLD, ge=0, le=100),
    db: Session = Depends(get_db)
):
    if target_date:
        try:
            date_obj = datetime.strptime(target_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="日期格式错误，请使用 YYYY-MM-DD")
    else:
        date_obj = date.today()

    start_of_day = datetime.combine(date_obj, datetime.min.time())
    end_of_day = datetime.combine(date_obj + timedelta(days=1), datetime.min.time())

    q = db.query(RumorCase).filter(
        RumorCase.risk_score >= min_risk_score,
        RumorCase.last_active >= start_of_day,
        RumorCase.last_active < end_of_day
    )

    if category:
        if category not in settings.CATEGORIES:
            raise HTTPException(status_code=400, detail=f"无效的类别，有效值为: {settings.CATEGORIES}")
        q = q.filter(RumorCase.category == category)

    rumors = q.order_by(RumorCase.risk_score.desc()).all()

    by_category = defaultdict(int)
    rumor_list = []

    for r in rumors:
        by_category[r.category] += 1
        rumor_list.append(HighRiskRumor(
            rumor_id=r.id,
            title=r.title,
            category=r.category,
            risk_level=r.risk_level,
            risk_score=r.risk_score,
            first_seen=r.first_seen,
            last_active=r.last_active,
            total_shares=r.total_shares,
            affected_regions=r.affected_regions or [],
            debunk_status=r.debunk_status,
            handle_status=r.handle_status
        ))

    return DailyHighRiskResponse(
        date=date_obj.isoformat(),
        total_high_risk=len(rumors),
        by_category=dict(by_category),
        rumors=rumor_list
    )


@router.get("/rumors/{rumor_id}/spread-track", response_model=SpreadTrackResponse)
def get_spread_track(rumor_id: int, db: Session = Depends(get_db)):
    rumor = db.query(RumorCase).filter(RumorCase.id == rumor_id).first()
    if not rumor:
        raise HTTPException(status_code=404, detail="谣言案例不存在")

    spread_records = db.query(SpreadRecord).filter(
        SpreadRecord.rumor_case_id == rumor_id
    ).order_by(SpreadRecord.timestamp).all()

    if not spread_records:
        return SpreadTrackResponse(
            rumor_id=rumor_id,
            title=rumor.title,
            intervention_time=rumor.intervention_time,
            trend=[],
            effect_evaluation=None
        )

    daily_agg = defaultdict(lambda: {"share_count": 0, "after_intervention": False})
    for sr in spread_records:
        day = sr.timestamp.date()
        daily_agg[day]["share_count"] += sr.share_count
        if sr.after_intervention:
            daily_agg[day]["after_intervention"] = True

    trend = []
    for day in sorted(daily_agg.keys()):
        data = daily_agg[day]
        trend.append(SpreadTrendItem(
            timestamp=datetime.combine(day, datetime.min.time()),
            share_count=data["share_count"],
            after_intervention=data["after_intervention"]
        ))

    effect_evaluation = None
    if rumor.intervention_time:
        pre_intervention = [t for t in trend if t.timestamp < rumor.intervention_time]
        post_intervention = [t for t in trend if t.timestamp >= rumor.intervention_time]

        if pre_intervention and post_intervention:
            pre_avg = sum(t.share_count for t in pre_intervention) / len(pre_intervention)
            post_avg = sum(t.share_count for t in post_intervention) / len(post_intervention)
            reduction_rate = (pre_avg - post_avg) / pre_avg * 100 if pre_avg > 0 else 0

            if reduction_rate > 50:
                effect_evaluation = f"处置效果显著，传播量下降约{int(reduction_rate)}%"
            elif reduction_rate > 20:
                effect_evaluation = f"处置有一定效果，传播量下降约{int(reduction_rate)}%"
            elif reduction_rate >= 0:
                effect_evaluation = f"处置效果一般，传播量仅下降约{int(reduction_rate)}%"
            else:
                effect_evaluation = f"处置效果不佳，传播量反而上升约{int(abs(reduction_rate))}%"

    return SpreadTrackResponse(
        rumor_id=rumor_id,
        title=rumor.title,
        intervention_time=rumor.intervention_time,
        trend=trend,
        effect_evaluation=effect_evaluation
    )


@router.post("/rumors/{rumor_id}/intervene")
def record_intervention(rumor_id: int, db: Session = Depends(get_db)):
    rumor = db.query(RumorCase).filter(RumorCase.id == rumor_id).first()
    if not rumor:
        raise HTTPException(status_code=404, detail="谣言案例不存在")

    rumor.intervention_time = datetime.utcnow()
    rumor.handle_status = "已处置"
    db.commit()

    return {
        "status": "success",
        "message": "处置记录已保存",
        "intervention_time": rumor.intervention_time
    }


@router.get("/rumors/{rumor_id}/duplicate-accounts")
def get_duplicate_accounts(rumor_id: int, db: Session = Depends(get_db)):
    rumor = db.query(RumorCase).filter(RumorCase.id == rumor_id).first()
    if not rumor:
        raise HTTPException(status_code=404, detail="谣言案例不存在")

    accounts = sorted(rumor.duplicate_accounts, key=lambda x: x.post_count, reverse=True)

    return {
        "rumor_id": rumor_id,
        "title": rumor.title,
        "total_accounts": len(accounts),
        "accounts": [
            {
                "account_id": a.account_id,
                "account_name": a.account_name,
                "platform": a.platform,
                "post_count": a.post_count,
                "first_post_time": a.first_post_time,
                "last_post_time": a.last_post_time
            }
            for a in accounts
        ]
    }


@router.get("/rumors", response_model=List[HighRiskRumor])
def list_rumors(
    category: Optional[str] = None,
    risk_level: Optional[str] = None,
    handle_status: Optional[str] = None,
    sort_by: str = Query("risk_score", pattern="^(risk_score|first_seen|last_active|total_shares)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    q = db.query(RumorCase)

    if category:
        q = q.filter(RumorCase.category == category)
    if risk_level:
        q = q.filter(RumorCase.risk_level == risk_level)
    if handle_status:
        q = q.filter(RumorCase.handle_status == handle_status)

    sort_column = getattr(RumorCase, sort_by)
    if sort_order == "desc":
        sort_column = sort_column.desc()

    rumors = q.order_by(sort_column).offset(skip).limit(limit).all()

    return [
        HighRiskRumor(
            rumor_id=r.id,
            title=r.title,
            category=r.category,
            risk_level=r.risk_level,
            risk_score=r.risk_score,
            first_seen=r.first_seen,
            last_active=r.last_active,
            total_shares=r.total_shares,
            affected_regions=r.affected_regions or [],
            debunk_status=r.debunk_status,
            handle_status=r.handle_status
        )
        for r in rumors
    ]
