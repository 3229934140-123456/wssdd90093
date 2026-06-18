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
    DailyTrendItem, CategoryTrendItem,
    HandleEffectSummary, StageSummaryItem, CategoryStageItem,
    ReviewEntryResponse, ReviewCaseItem
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


MANUAL_STAGES = ["已压降", "处置无效"]


def _evaluate_system_stage(r: RumorCase) -> str:
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


def _compute_handle_stage(r: RumorCase) -> str:
    system_stage = _evaluate_system_stage(r)
    manual_stage = getattr(r, "handle_stage", None)
    if manual_stage in MANUAL_STAGES and manual_stage != system_stage:
        return manual_stage
    return system_stage


def _get_stage_with_override(r: RumorCase) -> tuple:
    system_stage = _evaluate_system_stage(r)
    manual_stage = getattr(r, "handle_stage", None)
    overridden = (manual_stage in MANUAL_STAGES) and (manual_stage != system_stage)
    final_stage = manual_stage if overridden else system_stage
    return final_stage, system_stage, overridden


def _build_high_rumor(r: RumorCase) -> HighRiskRumor:
    channels = [c.get("channel_name", "") for c in (r.main_channels or [])]
    recent_growth = None
    if r.main_channels and len(r.main_channels) > 0:
        growth_rates = [c.get("growth_rate", 0) for c in r.main_channels]
        recent_growth = max(growth_rates) if growth_rates else None

    final_stage, system_stage, overridden = _get_stage_with_override(r)
    suggested_action = STAGE_SUGGESTED_ACTIONS.get(final_stage)

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
        handle_stage=final_stage,
        system_evaluated_stage=system_stage if overridden else None,
        stage_overridden=overridden,
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
    handle_stage: Optional[str] = Query(None, pattern="^(待处置|观察中|已压降|处置无效)$"),
    sort_by: str = Query("risk_score", pattern="^(risk_score|total_shares|last_active)$"),
    db: Session = Depends(get_db)
):
    date_obj = _parse_date(target_date)
    rumors = _get_daily_rumors(date_obj, min_risk_score, category, db)

    high_rumors = [_build_high_rumor(r) for r in rumors]

    if handle_stage:
        high_rumors = [r for r in high_rumors if r.handle_stage == handle_stage]

    by_category = defaultdict(int)
    for r in high_rumors:
        by_category[r.category] += 1

    if sort_by == "risk_score":
        high_rumors.sort(key=lambda x: x.risk_score, reverse=True)
    elif sort_by == "total_shares":
        high_rumors.sort(key=lambda x: x.total_shares, reverse=True)
    elif sort_by == "last_active":
        high_rumors.sort(key=lambda x: x.last_active, reverse=True)

    return DailyHighRiskResponse(
        date=date_obj.isoformat(),
        total_high_risk=len(high_rumors),
        by_category=dict(by_category),
        rumors=high_rumors
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
    handle_stage: Optional[str] = Query(None, pattern="^(待处置|观察中|已压降|处置无效)$"),
    group_by_category: bool = True,
    include_feedback_summary: bool = True,
    db: Session = Depends(get_db)
):
    date_obj = _parse_date(target_date)
    rumors = _get_daily_rumors(date_obj, min_risk_score, None, db)

    high_rumors = [_build_high_rumor(r) for r in rumors]

    if handle_stage:
        high_rumors = [r for r in high_rumors if r.handle_stage == handle_stage]

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

    if include_feedback_summary:
        feedbacks = db.query(TipFeedback).all()
        type_stats = defaultdict(lambda: {"total": 0, "准确": 0, "不准确": 0, "已采用": 0})
        for f in feedbacks:
            type_stats[f.tip_type]["total"] += 1
            type_stats[f.tip_type][f.feedback] += 1

        feedback_summary = []
        for tip_type, stats in sorted(type_stats.items(), key=lambda x: x[1]["已采用"], reverse=True):
            total = stats["total"]
            adopted = stats["已采用"]
            accurate = stats["准确"]
            inaccurate = stats["不准确"]
            adoption_rate = round(adopted / total * 100, 1) if total > 0 else 0
            accurate_rate = round(accurate / total * 100, 1) if total > 0 else 0
            inaccurate_rate = round(inaccurate / total * 100, 1) if total > 0 else 0
            feedback_summary.append({
                "tip_type": tip_type,
                "total": total,
                "adopted": adopted,
                "adoption_rate": adoption_rate,
                "accurate": accurate,
                "accurate_rate": accurate_rate,
                "inaccurate": inaccurate,
                "inaccurate_rate": inaccurate_rate
            })
        export_data["tip_feedback_summary"] = feedback_summary
        export_data["top_adopted_tips"] = feedback_summary[:3]

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

        if include_feedback_summary and export_data.get("tip_feedback_summary"):
            writer.writerow([])
            writer.writerow(["=== 复核反馈摘要 ==="])
            writer.writerow(["提示类型", "总反馈数", "已采用数", "采用率", "准确数", "准确占比", "不准确数", "不准确占比"])
            for item in export_data["tip_feedback_summary"]:
                writer.writerow([
                    item["tip_type"],
                    item["total"],
                    item["adopted"],
                    f"{item['adoption_rate']}%",
                    item["accurate"],
                    f"{item['accurate_rate']}%",
                    item["inaccurate"],
                    f"{item['inaccurate_rate']}%"
                ])
            if export_data.get("top_adopted_tips"):
                writer.writerow([])
                writer.writerow(["=== 采用率TOP提示类型 ==="])
                for i, item in enumerate(export_data["top_adopted_tips"], 1):
                    writer.writerow([
                        f"TOP{i}",
                        item["tip_type"],
                        f"采用率 {item['adoption_rate']}%",
                        f"准确占比 {item['accurate_rate']}%"
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
    category: Optional[str] = None,
    handle_stage: Optional[str] = Query(None, pattern="^(待处置|观察中|已压降|处置无效)$"),
    db: Session = Depends(get_db)
):
    if category and category not in settings.CATEGORIES:
        raise HTTPException(status_code=400, detail=f"无效的类别，有效值为: {settings.CATEGORIES}")

    end_date = date.today()
    start_date = end_date - timedelta(days=days - 1)

    overall_trend = []
    category_data: Dict[str, List[DailyTrendItem]] = defaultdict(list)

    for i in range(days):
        current_date = start_date + timedelta(days=i)
        start_of_day = datetime.combine(current_date, datetime.min.time())
        end_of_day = datetime.combine(current_date + timedelta(days=1), datetime.min.time())

        q = db.query(RumorCase).filter(
            RumorCase.risk_score >= min_risk_score,
            RumorCase.last_active >= start_of_day,
            RumorCase.last_active < end_of_day
        )
        if category:
            q = q.filter(RumorCase.category == category)

        day_rumors = q.all()

        if handle_stage:
            day_rumors = [r for r in day_rumors if _compute_handle_stage(r) == handle_stage]

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

        cat_group: Dict[str, List[RumorCase]] = defaultdict(list)
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

    date_list = [(start_date + timedelta(days=i)).isoformat() for i in range(days)]

    target_categories = [category] if category else settings.CATEGORIES
    category_trends = []
    for cat in target_categories:
        cat_daily = category_data.get(cat, [])
        cat_by_date = {item.date: item for item in cat_daily}

        full_daily = []
        for d in date_list:
            if d in cat_by_date:
                full_daily.append(cat_by_date[d])
            else:
                full_daily.append(DailyTrendItem(
                    date=d,
                    high_risk_count=0,
                    avg_risk_score=0.0,
                    total_shares=0
                ))

        trend_direction, change_rate = _compute_trend_direction(full_daily)
        category_trends.append(CategoryTrendItem(
            category=cat,
            daily_trend=full_daily,
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
        if second_avg > 0:
            return "明显升高", None
        else:
            return "平稳", None

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


def _calculate_reduction_rate(r: RumorCase) -> Optional[float]:
    if not r.intervention_time or not r.spread_records:
        return None

    pre = [sr for sr in r.spread_records if sr.timestamp < r.intervention_time]
    post = [sr for sr in r.spread_records if sr.timestamp >= r.intervention_time]

    if not pre or not post:
        return None

    pre_days = len(set(sr.timestamp.date() for sr in pre))
    post_days = len(set(sr.timestamp.date() for sr in post))

    if pre_days == 0 or post_days == 0:
        return None

    pre_avg = sum(sr.share_count for sr in pre) / pre_days
    post_avg = sum(sr.share_count for sr in post) / post_days

    if pre_avg == 0:
        return 0.0

    return round((pre_avg - post_avg) / pre_avg * 100, 1)


@router.get("/handle-effect-summary", response_model=HandleEffectSummary)
def get_handle_effect_summary(
    target_date: Optional[str] = None,
    min_risk_score: int = Query(settings.HIGH_RISK_THRESHOLD, ge=0, le=100),
    db: Session = Depends(get_db)
):
    date_obj = _parse_date(target_date)
    rumors = _get_daily_rumors(date_obj, min_risk_score, None, db)

    stage_stats: Dict[str, List[RumorCase]] = defaultdict(list)
    for r in rumors:
        stage = _compute_handle_stage(r)
        stage_stats[stage].append(r)

    by_stage = []
    all_reductions = []
    for stage in HANDLE_STAGES:
        stage_rumors = stage_stats.get(stage, [])
        reductions = []
        for r in stage_rumors:
            rate = _calculate_reduction_rate(r)
            if rate is not None:
                reductions.append(rate)
                all_reductions.append(rate)

        avg_reduction = round(sum(reductions) / len(reductions), 1) if reductions else None
        by_stage.append(StageSummaryItem(
            stage=stage,
            count=len(stage_rumors),
            avg_reduction_rate=avg_reduction
        ))

    ineffective_rumors = stage_stats.get("处置无效", [])
    ineffective_by_cat: Dict[str, List[RumorCase]] = defaultdict(list)
    for r in ineffective_rumors:
        ineffective_by_cat[r.category].append(r)

    ineffective_by_category = []
    for cat in settings.CATEGORIES:
        cat_rumors = ineffective_by_cat.get(cat, [])
        if cat_rumors:
            avg_score = round(sum(r.risk_score for r in cat_rumors) / len(cat_rumors), 1)
            ineffective_by_category.append(CategoryStageItem(
                category=cat,
                count=len(cat_rumors),
                avg_risk_score=avg_score
            ))
    ineffective_by_category.sort(key=lambda x: x.count, reverse=True)

    overall_avg_reduction = round(sum(all_reductions) / len(all_reductions), 1) if all_reductions else None

    effective_count = len(stage_stats.get("已压降", []))
    closed_count = effective_count + len(stage_stats.get("处置无效", []))
    effective_rate = round(effective_count / closed_count * 100, 1) if closed_count > 0 else None

    return HandleEffectSummary(
        date=date_obj.isoformat(),
        total_high_risk=len(rumors),
        by_stage=by_stage,
        ineffective_by_category=ineffective_by_category,
        overall_avg_reduction=overall_avg_reduction,
        effective_rate=effective_rate
    )


NEXT_ACTIONS_BY_CATEGORY = {
    "医疗健康": "建议联合卫健委核实权威信息，升级辟谣内容覆盖重点社群",
    "公共安全": "建议协调公安宣传部门，联动地方政务号集中发声压制不实信息",
    "民生政策": "建议联系政策发布单位补充说明原文，对重点转发账号点对点沟通",
    "财经金融": "建议联动证监会或行业协会发布澄清公告，通知主要财经平台限流",
    "教育文化": "建议联系教育主管部门说明情况，通过校园渠道定向推送辟谣",
    "其他": "建议复核案例分类是否准确，根据实际传播渠道制定针对性处置方案"
}


@router.get("/review-entry/{category}", response_model=ReviewEntryResponse)
def get_review_entry(
    category: str,
    target_date: Optional[str] = None,
    min_risk_score: int = Query(settings.HIGH_RISK_THRESHOLD, ge=0, le=100),
    db: Session = Depends(get_db)
):
    if category not in settings.CATEGORIES:
        raise HTTPException(status_code=400, detail=f"无效的类别，有效值为: {settings.CATEGORIES}")

    date_obj = _parse_date(target_date)
    rumors = _get_daily_rumors(date_obj, min_risk_score, None, db)

    ineffective_rumors = []
    for r in rumors:
        final_stage, _, _ = _get_stage_with_override(r)
        if r.category == category and final_stage == "处置无效":
            ineffective_rumors.append(r)

    cases = []
    for r in ineffective_rumors:
        channels = [c.get("channel_name", "") for c in (r.main_channels or [])]
        reduction = _calculate_reduction_rate(r)

        if reduction is not None and reduction < 0:
            recent_change = f"处置后传播量反而上升 {int(abs(reduction))}%"
        elif reduction is not None:
            recent_change = f"处置后仅下降 {int(reduction)}%"
        else:
            recent_change = "传播数据不足，无法量化变化"

        base_action = NEXT_ACTIONS_BY_CATEGORY.get(category, NEXT_ACTIONS_BY_CATEGORY["其他"])
        if reduction is not None and reduction < -10:
            suggested_next_action = f"{base_action}；当前传播仍在扩散，建议 48 小时内完成复盘"
        elif reduction is not None and reduction < 5:
            suggested_next_action = f"{base_action}；处置效果不明显，建议本周内安排联合复盘"
        else:
            suggested_next_action = f"{base_action}；建议核查分类是否有误，重新评估处置策略"

        cases.append(ReviewCaseItem(
            rumor_id=r.id,
            title=r.title,
            risk_level=r.risk_level,
            risk_score=r.risk_score,
            total_shares=r.total_shares,
            reduction_rate=reduction,
            recent_change=recent_change,
            intervention_time=r.intervention_time,
            suggested_next_action=suggested_next_action,
            main_channels=channels[:3]
        ))

    cases.sort(key=lambda c: c.risk_score, reverse=True)

    avg_score = round(sum(c.risk_score for c in cases) / len(cases), 1) if cases else 0.0

    return ReviewEntryResponse(
        category=category,
        total_count=len(cases),
        avg_risk_score=avg_score,
        cases=cases
    )
