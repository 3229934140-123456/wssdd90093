from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, date, timedelta
from typing import List, Optional, Dict
from collections import defaultdict
import io
import csv
from database import get_db
from schemas import (
    DailyHighRiskResponse, HighRiskRumor,
    SpreadTrackResponse, SpreadTrendItem,
    DailyHighRiskGroupedResponse, CategoryGroup,
    InterventionStats, TipFeedbackCreate, TipFeedbackResponse,
    TipFeedbackSummary, CrossDayComparisonResponse,
    DailyTrendItem, CategoryTrendItem
)
from models import RumorCase, SpreadRecord, DuplicateAccount, TipFeedback
from config import settings

router = APIRouter(prefix="/supervisor", tags=["主管视图"])

HANDLE_STAGES = ["待处置", "观察中", "已压降", "处置无效"]

STAGE_SUGGESTED_ACTIONS = {
    "待处置": "建议尽快安排处置，关注传播趋势",
    "观察中": "处置后观察期，持续关注传播变化",
    "已压降": "传播已控制，可转为定期巡检",
    "处置无效": "处置效果不佳，建议调整策略或升级处置"
}


def _parse_date(target_date: Optional[str]) -> date:
    if target_date:
        try:
            return datetime.strptime(target_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="日期格式错误，请使用 YYYY-MM-DD")
    return date.today()


def _compute_handle_stage(r: RumorCase) -> str:
    if not r.intervention_time:
        return "待处置"

    spread_records = r.spread_records
    if not spread_records:
        return "观察中"

    now = datetime.now()
    days_since = (now - r.intervention_time).days

    if days_since < 3:
        return "观察中"

    pre = [sr for sr in spread_records if sr.timestamp < r.intervention_time]
    post = [sr for sr in spread_records if sr.timestamp >= r.intervention_time]

    if not post:
        return "观察中"

    if pre:
        pre_total = sum(sr.share_count for sr in pre)
        pre_avg = pre_total / max(len(set(sr.timestamp.date() for sr in pre)), 1)
        post_total = sum(sr.share_count for sr in post)
        post_days = len(set(sr.timestamp.date() for sr in post))
        post_avg = post_total / max(post_days, 1)

        reduction = (pre_avg - post_avg) / pre_avg * 100 if pre_avg > 0 else 0

        if reduction > 30:
            return "已压降"
        elif reduction < 5:
            return "处置无效"

    return "观察中"


def _build_high_rumor(r: RumorCase) -> HighRiskRumor:
    channels = [c.get("channel_name", "") for c in (r.main_channels or [])]
    recent_growth = None
    if r.main_channels and len(r.main_channels) > 0:
        growth_rates = [c.get("growth_rate", 0) for c in r.main_channels]
        recent_growth = max(growth_rates) if growth_rates else None

    stage = _compute_handle_stage(r)
    suggested_action = STAGE_SUGGESTED_ACTIONS.get(stage)

    return HighRiskRumor(
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
        handle_status=r.handle_status,
        handle_stage=stage,
        main_channels=channels,
        recent_growth_rate=recent_growth,
        suggested_action=suggested_action
    )


def _get_daily_rumors(date_obj: date, min_risk_score: int,
                      category: Optional[str], db: Session) -> List[RumorCase]:
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

    return q.all()


@router.get("/daily-high-risk", response_model=DailyHighRiskResponse)
def get_daily_high_risk(
    target_date: Optional[str] = None,
    category: Optional[str] = None,
    min_risk_score: int = Query(settings.HIGH_RISK_THRESHOLD, ge=0, le=100),
    sort_by: str = Query("risk_score", pattern="^(risk_score|total_shares|last_active)$"),
    db: Session = Depends(get_db)
):
    date_obj = _parse_date(target_date)
    rumors = _get_daily_rumors(date_obj, min_risk_score, category, db)

    by_category = defaultdict(int)
    rumor_list = []

    for r in rumors:
        by_category[r.category] += 1
        rumor_list.append(_build_high_rumor(r))

    if sort_by == "risk_score":
        rumor_list.sort(key=lambda x: x.risk_score, reverse=True)
    elif sort_by == "total_shares":
        rumor_list.sort(key=lambda x: x.total_shares, reverse=True)
    elif sort_by == "last_active":
        rumor_list.sort(key=lambda x: x.last_active, reverse=True)

    return DailyHighRiskResponse(
        date=date_obj.isoformat(),
        total_high_risk=len(rumors),
        by_category=dict(by_category),
        rumors=rumor_list
    )


@router.get("/daily-high-risk/grouped", response_model=DailyHighRiskGroupedResponse)
def get_daily_high_risk_grouped(
    target_date: Optional[str] = None,
    min_risk_score: int = Query(settings.HIGH_RISK_THRESHOLD, ge=0, le=100),
    handle_stage: Optional[str] = Query(None, pattern="^(待处置|观察中|已压降|处置无效)$"),
    group_sort_by: str = Query("risk_score", pattern="^(risk_score|total_shares|count)$"),
    item_sort_by: str = Query("risk_score", pattern="^(risk_score|total_shares)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db)
):
    date_obj = _parse_date(target_date)
    rumors = _get_daily_rumors(date_obj, min_risk_score, None, db)

    high_rumors = [_build_high_rumor(r) for r in rumors]

    if handle_stage:
        high_rumors = [r for r in high_rumors if r.handle_stage == handle_stage]

    groups_dict: Dict[str, List[HighRiskRumor]] = defaultdict(list)
    by_category = defaultdict(int)

    for r in high_rumors:
        groups_dict[r.category].append(r)
        by_category[r.category] += 1

    groups = []
    for cat in settings.CATEGORIES:
        if cat in groups_dict:
            cat_rumors = groups_dict[cat]

            if item_sort_by == "risk_score":
                cat_rumors.sort(key=lambda x: x.risk_score, reverse=(sort_order == "desc"))
            elif item_sort_by == "total_shares":
                cat_rumors.sort(key=lambda x: x.total_shares, reverse=(sort_order == "desc"))

            groups.append(CategoryGroup(
                category=cat,
                count=len(cat_rumors),
                rumors=cat_rumors
            ))

    if group_sort_by == "count":
        groups.sort(key=lambda g: g.count, reverse=True)
    elif group_sort_by == "risk_score":
        groups.sort(key=lambda g: max([r.risk_score for r in g.rumors]) if g.rumors else 0, reverse=True)
    elif group_sort_by == "total_shares":
        groups.sort(key=lambda g: sum([r.total_shares for r in g.rumors]), reverse=True)

    return DailyHighRiskGroupedResponse(
        date=date_obj.isoformat(),
        total_high_risk=len(high_rumors),
        by_category=dict(by_category),
        groups=groups,
        sort_by=item_sort_by
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
            effect_evaluation=None,
            intervention_stats=None,
            observation_status="待观察"
        )

    if rumor.intervention_time:
        trend = _build_trend_split_by_intervention(spread_records, rumor.intervention_time)
    else:
        daily_agg = defaultdict(int)
        for sr in spread_records:
            daily_agg[sr.timestamp.date()] += sr.share_count

        trend = []
        for day in sorted(daily_agg.keys()):
            trend.append(SpreadTrendItem(
                timestamp=datetime.combine(day, datetime.min.time()),
                share_count=daily_agg[day],
                after_intervention=False
            ))

    intervention_stats = None
    effect_evaluation = None
    observation_status = "待观察"

    if rumor.intervention_time:
        pre_items = [t for t in trend if not t.after_intervention]
        post_items = [t for t in trend if t.after_intervention]

        pre_days = len(pre_items)
        post_days = len(post_items)

        pre_total = sum(t.share_count for t in pre_items)
        post_total = sum(t.share_count for t in post_items)

        pre_avg = pre_total / pre_days if pre_days > 0 else 0

        if post_days == 0:
            observation_status = "待观察"
            effect_evaluation = "刚刚处置，暂无后续传播数据，请稍后查看"
        elif post_days < 3:
            observation_status = "待观察"
            post_avg = post_total / post_days
            reduction_rate = (pre_avg - post_avg) / pre_avg * 100 if pre_avg > 0 else 0
            effect_evaluation = f"处置后观察期较短（{post_days}天），暂为下降{int(reduction_rate)}%，建议继续观察"
        else:
            observation_status = "充足"
            post_avg = post_total / post_days
            reduction_rate = (pre_avg - post_avg) / pre_avg * 100 if pre_avg > 0 else 0

            if reduction_rate > 50:
                effect_evaluation = f"处置效果显著，传播量下降约{int(reduction_rate)}%"
            elif reduction_rate > 20:
                effect_evaluation = f"处置有一定效果，传播量下降约{int(reduction_rate)}%"
            elif reduction_rate >= 0:
                effect_evaluation = f"处置效果一般，传播量仅下降约{int(reduction_rate)}%"
            else:
                effect_evaluation = f"处置效果不佳，传播量反而上升约{int(abs(reduction_rate))}%"

        intervention_stats = InterventionStats(
            pre_avg_daily=round(pre_avg, 1) if pre_days > 0 else None,
            post_new_shares=post_total if post_days > 0 else None,
            pre_period_days=pre_days if pre_days > 0 else None,
            post_period_days=post_days if post_days > 0 else None
        )

        if pre_days > 0 and post_days > 0:
            post_avg = post_total / post_days
            reduction_rate = (pre_avg - post_avg) / pre_avg * 100 if pre_avg > 0 else 0
            intervention_stats.reduction_rate = round(reduction_rate, 1)

    return SpreadTrackResponse(
        rumor_id=rumor_id,
        title=rumor.title,
        intervention_time=rumor.intervention_time,
        trend=trend,
        effect_evaluation=effect_evaluation,
        intervention_stats=intervention_stats,
        observation_status=observation_status
    )


def _build_trend_split_by_intervention(records: List[SpreadRecord],
                                        intervention_time: datetime) -> List[SpreadTrendItem]:
    pre_agg: Dict = defaultdict(int)
    post_agg: Dict = defaultdict(int)

    intervention_date = intervention_time.date()

    for sr in records:
        day = sr.timestamp.date()
        if sr.timestamp < intervention_time:
            pre_agg[day] += sr.share_count
        else:
            post_agg[day] += sr.share_count

    all_days = set(pre_agg.keys()) | set(post_agg.keys())
    trend = []

    for day in sorted(all_days):
        pre_count = pre_agg.get(day, 0)
        post_count = post_agg.get(day, 0)

        if day < intervention_date:
            trend.append(SpreadTrendItem(
                timestamp=datetime.combine(day, datetime.min.time()),
                share_count=pre_count,
                after_intervention=False
            ))
        elif day == intervention_date:
            if pre_count > 0:
                trend.append(SpreadTrendItem(
                    timestamp=datetime.combine(day, datetime.min.time()),
                    share_count=pre_count,
                    after_intervention=False
                ))
            if post_count > 0:
                trend.append(SpreadTrendItem(
                    timestamp=datetime.combine(day, datetime.min.time()),
                    share_count=post_count,
                    after_intervention=True
                ))
        else:
            trend.append(SpreadTrendItem(
                timestamp=datetime.combine(day, datetime.min.time()),
                share_count=post_count,
                after_intervention=True
            ))

    return trend


@router.post("/rumors/{rumor_id}/intervene")
def record_intervention(rumor_id: int, db: Session = Depends(get_db)):
    rumor = db.query(RumorCase).filter(RumorCase.id == rumor_id).first()
    if not rumor:
        raise HTTPException(status_code=404, detail="谣言案例不存在")

    rumor.intervention_time = datetime.now()
    rumor.handle_status = "已处置"
    rumor.handle_stage = "观察中"
    db.commit()

    return {
        "status": "success",
        "message": "处置记录已保存",
        "intervention_time": rumor.intervention_time,
        "handle_stage": rumor.handle_stage
    }


@router.post("/rumors/{rumor_id}/update-stage")
def update_handle_stage(rumor_id: int, stage: str = Query(..., pattern="^(待处置|观察中|已压降|处置无效)$"),
                        db: Session = Depends(get_db)):
    rumor = db.query(RumorCase).filter(RumorCase.id == rumor_id).first()
    if not rumor:
        raise HTTPException(status_code=404, detail="谣言案例不存在")

    rumor.handle_stage = stage
    if stage in ["已压降", "处置无效"]:
        rumor.handle_status = "已处置"
    db.commit()

    return {
        "status": "success",
        "rumor_id": rumor_id,
        "handle_stage": stage,
        "suggested_action": STAGE_SUGGESTED_ACTIONS.get(stage, "")
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
    handle_stage: Optional[str] = Query(None, pattern="^(待处置|观察中|已压降|处置无效)$"),
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

    result = [_build_high_rumor(r) for r in rumors]

    if handle_stage:
        result = [r for r in result if r.handle_stage == handle_stage]

    return result


@router.get("/daily-high-risk/export")
def export_daily_high_risk(
    target_date: Optional[str] = None,
    format: str = Query("json", pattern="^(json|csv)$"),
    min_risk_score: int = Query(settings.HIGH_RISK_THRESHOLD, ge=0, le=100),
    group_by_category: bool = True,
    db: Session = Depends(get_db)
):
    date_obj = _parse_date(target_date)
    rumors = _get_daily_rumors(date_obj, min_risk_score, None, db)

    high_rumors = [_build_high_rumor(r) for r in rumors]

    def _rumor_to_dict(r: HighRiskRumor) -> dict:
        return {
            "rumor_id": r.rumor_id,
            "title": r.title,
            "risk_level": r.risk_level,
            "risk_score": r.risk_score,
            "total_shares": r.total_shares,
            "main_channels": ", ".join(r.main_channels[:3]),
            "handle_status": r.handle_status,
            "handle_stage": r.handle_stage,
            "suggested_action": r.suggested_action or "",
            "debunk_status": r.debunk_status,
            "recent_growth_rate": f"{int(r.recent_growth_rate * 100)}%" if r.recent_growth_rate else "N/A",
            "first_seen": r.first_seen.isoformat(),
            "last_active": r.last_active.isoformat()
        }

    export_data = {
        "export_date": date_obj.isoformat(),
        "total_high_risk": len(high_rumors),
        "export_time": datetime.now().isoformat(),
        "data": []
    }

    if group_by_category:
        groups = defaultdict(list)
        for r in high_rumors:
            groups[r.category].append(r)

        export_data["by_category_count"] = {k: len(v) for k, v in groups.items()}
        export_data["groups"] = []

        for cat in settings.CATEGORIES:
            if cat in groups:
                cat_rumors = sorted(groups[cat], key=lambda x: x.risk_score, reverse=True)
                export_data["groups"].append({
                    "category": cat,
                    "count": len(cat_rumors),
                    "rumors": [_rumor_to_dict(r) for r in cat_rumors]
                })
    else:
        sorted_rumors = sorted(high_rumors, key=lambda x: x.risk_score, reverse=True)
        export_data["data"] = [
            {**_rumor_to_dict(r), "category": r.category,
             "affected_regions": ", ".join(r.affected_regions[:3])}
            for r in sorted_rumors
        ]

    if format == "json":
        filename = f"high_risk_rumors_{date_obj.isoformat()}.json"
        return JSONResponse(
            content=export_data,
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    else:
        filename = f"high_risk_rumors_{date_obj.isoformat()}.csv"

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "序号", "类别", "标题", "风险等级", "风险分值",
            "总传播量", "主要通道", "处置状态", "处置阶段",
            "建议动作", "辟谣状态", "近期增长率",
            "受影响地区", "首次出现", "最后活跃"
        ])

        all_rumors = []
        if group_by_category:
            for group in export_data.get("groups", []):
                for r in group["rumors"]:
                    r["category"] = group["category"]
                    all_rumors.append(r)
        else:
            all_rumors = export_data["data"]

        for idx, r in enumerate(all_rumors, 1):
            writer.writerow([
                idx,
                r.get("category", ""),
                r["title"],
                r["risk_level"],
                r["risk_score"],
                r["total_shares"],
                r["main_channels"],
                r["handle_status"],
                r.get("handle_stage", ""),
                r.get("suggested_action", ""),
                r["debunk_status"],
                r["recent_growth_rate"],
                r.get("affected_regions", ""),
                r["first_seen"],
                r["last_active"]
            ])

        csv_content = output.getvalue()
        output.close()

        return StreamingResponse(
            iter([csv_content]),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )


@router.get("/cross-day-comparison", response_model=CrossDayComparisonResponse)
def cross_day_comparison(
    days: int = Query(7, ge=7, le=30),
    min_risk_score: int = Query(settings.HIGH_RISK_THRESHOLD, ge=0, le=100),
    db: Session = Depends(get_db)
):
    end_date = date.today()
    start_date = end_date - timedelta(days=days - 1)

    overall_trend = []
    category_data: Dict[str, List[DailyTrendItem]] = defaultdict(list)

    for i in range(days):
        current_date = start_date + timedelta(days=i)
        start_of_day = datetime.combine(current_date, datetime.min.time())
        end_of_day = datetime.combine(current_date + timedelta(days=1), datetime.min.time())

        day_rumors = db.query(RumorCase).filter(
            RumorCase.risk_score >= min_risk_score,
            RumorCase.last_active >= start_of_day,
            RumorCase.last_active < end_of_day
        ).all()

        high_risk_count = len(day_rumors)
        avg_score = sum(r.risk_score for r in day_rumors) / high_risk_count if high_risk_count > 0 else 0
        total_shares = sum(r.total_shares for r in day_rumors)

        item = DailyTrendItem(
            date=current_date.isoformat(),
            high_risk_count=high_risk_count,
            avg_risk_score=round(avg_score, 1),
            total_shares=total_shares
        )
        overall_trend.append(item)

        cat_group = defaultdict(list)
        for r in day_rumors:
            cat_group[r.category].append(r)

        for cat, cat_rumors in cat_group.items():
            cat_avg = sum(r.risk_score for r in cat_rumors) / len(cat_rumors)
            cat_shares = sum(r.total_shares for r in cat_rumors)
            category_data[cat].append(DailyTrendItem(
                date=current_date.isoformat(),
                high_risk_count=len(cat_rumors),
                avg_risk_score=round(cat_avg, 1),
                total_shares=cat_shares
            ))

    category_trends = []
    for cat in settings.CATEGORIES:
        if cat in category_data:
            daily = category_data[cat]
            trend_direction, change_rate = _compute_trend_direction(daily)
            category_trends.append(CategoryTrendItem(
                category=cat,
                daily_trend=daily,
                trend_direction=trend_direction,
                change_rate=change_rate
            ))

    return CrossDayComparisonResponse(
        period_days=days,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        overall=overall_trend,
        by_category=category_trends
    )


def _compute_trend_direction(daily_items: List[DailyTrendItem]) -> tuple:
    if len(daily_items) < 2:
        return "平稳", None

    first_half = daily_items[:len(daily_items) // 2]
    second_half = daily_items[len(daily_items) // 2:]

    first_avg = sum(d.high_risk_count for d in first_half) / len(first_half)
    second_avg = sum(d.high_risk_count for d in second_half) / len(second_half)

    if first_avg == 0:
        return "上升" if second_avg > 0 else "平稳", None

    change_rate = round((second_avg - first_avg) / first_avg * 100, 1)

    if change_rate > 20:
        return "明显升高", change_rate
    elif change_rate > 5:
        return "小幅上升", change_rate
    elif change_rate < -20:
        return "明显下降", change_rate
    elif change_rate < -5:
        return "小幅下降", change_rate
    else:
        return "平稳", change_rate


@router.post("/tips/feedback", response_model=TipFeedbackResponse)
def submit_tip_feedback(feedback: TipFeedbackCreate, db: Session = Depends(get_db)):
    from models import AuditQuery
    query = db.query(AuditQuery).filter(AuditQuery.id == feedback.query_id).first()
    if not query:
        raise HTTPException(status_code=404, detail="查询记录不存在")

    record = TipFeedback(
        query_id=feedback.query_id,
        tip_type=feedback.tip_type,
        feedback=feedback.feedback,
        submitter=feedback.submitter
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    return TipFeedbackResponse(
        id=record.id,
        query_id=record.query_id,
        tip_type=record.tip_type,
        feedback=record.feedback,
        submitter=record.submitter,
        created_at=record.created_at
    )


@router.get("/tips/feedback/summary", response_model=List[TipFeedbackSummary])
def get_tip_feedback_summary(db: Session = Depends(get_db)):
    feedbacks = db.query(TipFeedback).all()

    type_stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {"total": 0, "准确": 0, "不准确": 0, "已采用": 0})

    for f in feedbacks:
        type_stats[f.tip_type]["total"] += 1
        type_stats[f.tip_type][f.feedback] += 1

    result = []
    for tip_type, stats in sorted(type_stats.items()):
        total = stats["total"]
        adopted = stats["已采用"]
        adoption_rate = round(adopted / total * 100, 1) if total > 0 else 0

        result.append(TipFeedbackSummary(
            tip_type=tip_type,
            total=total,
            accurate=stats["准确"],
            inaccurate=stats["不准确"],
            adopted=adopted,
            adoption_rate=adoption_rate
        ))

    return result
