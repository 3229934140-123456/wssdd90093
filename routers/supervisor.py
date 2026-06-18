from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session
from datetime import datetime, date, timedelta
from typing import List, Optional
from collections import defaultdict
import io
import csv
import json
from database import get_db
from schemas import (
    DailyHighRiskResponse, HighRiskRumor,
    SpreadTrackResponse, SpreadTrendItem,
    DailyHighRiskGroupedResponse, CategoryGroup,
    InterventionStats
)
from models import RumorCase, SpreadRecord, DuplicateAccount
from config import settings

router = APIRouter(prefix="/supervisor", tags=["主管视图"])


def _parse_date(target_date: Optional[str]) -> date:
    if target_date:
        try:
            return datetime.strptime(target_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="日期格式错误，请使用 YYYY-MM-DD")
    return date.today()


def _build_high_rumor(r: RumorCase) -> HighRiskRumor:
    channels = [c.get("channel_name", "") for c in (r.main_channels or [])]
    recent_growth = None
    if r.main_channels and len(r.main_channels) > 0:
        growth_rates = [c.get("growth_rate", 0) for c in r.main_channels]
        recent_growth = max(growth_rates) if growth_rates else None

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
        main_channels=channels,
        recent_growth_rate=recent_growth
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
    group_sort_by: str = Query("risk_score", pattern="^(risk_score|total_shares|count)$"),
    item_sort_by: str = Query("risk_score", pattern="^(risk_score|total_shares)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db)
):
    date_obj = _parse_date(target_date)
    rumors = _get_daily_rumors(date_obj, min_risk_score, None, db)

    groups_dict: Dict[str, List[RumorCase]] = defaultdict(list)
    by_category = defaultdict(int)

    for r in rumors:
        groups_dict[r.category].append(r)
        by_category[r.category] += 1

    groups = []
    for cat in settings.CATEGORIES:
        if cat in groups_dict:
            cat_rumors = groups_dict[cat]
            high_rumors = [_build_high_rumor(r) for r in cat_rumors]

            if item_sort_by == "risk_score":
                high_rumors.sort(key=lambda x: x.risk_score, reverse=(sort_order == "desc"))
            elif item_sort_by == "total_shares":
                high_rumors.sort(key=lambda x: x.total_shares, reverse=(sort_order == "desc"))

            groups.append(CategoryGroup(
                category=cat,
                count=len(cat_rumors),
                rumors=high_rumors
            ))

    if group_sort_by == "count":
        groups.sort(key=lambda g: g.count, reverse=True)
    elif group_sort_by == "risk_score":
        groups.sort(key=lambda g: max([r.risk_score for r in g.rumors]) if g.rumors else 0, reverse=True)
    elif group_sort_by == "total_shares":
        groups.sort(key=lambda g: sum([r.total_shares for r in g.rumors]), reverse=True)

    return DailyHighRiskGroupedResponse(
        date=date_obj.isoformat(),
        total_high_risk=len(rumors),
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
            observation_status="无数据"
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

    intervention_stats = None
    effect_evaluation = None
    observation_status = "充足"

    if rumor.intervention_time:
        pre_intervention = [t for t in trend if t.timestamp < rumor.intervention_time]
        post_intervention = [t for t in trend if t.timestamp >= rumor.intervention_time]

        if pre_intervention:
            pre_avg = sum(t.share_count for t in pre_intervention) / len(pre_intervention)
            pre_days = len(pre_intervention)
        else:
            pre_avg = 0
            pre_days = 0

        if post_intervention:
            post_total = sum(t.share_count for t in post_intervention)
            post_days = len(post_intervention)
        else:
            post_total = 0
            post_days = 0

        if post_days >= 3:
            observation_status = "充足"
        elif post_days >= 1:
            observation_status = "待观察"
        else:
            observation_status = "处置后暂无数据"

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

            if post_days >= 3:
                if reduction_rate > 50:
                    effect_evaluation = f"处置效果显著，传播量下降约{int(reduction_rate)}%"
                elif reduction_rate > 20:
                    effect_evaluation = f"处置有一定效果，传播量下降约{int(reduction_rate)}%"
                elif reduction_rate >= 0:
                    effect_evaluation = f"处置效果一般，传播量仅下降约{int(reduction_rate)}%"
                else:
                    effect_evaluation = f"处置效果不佳，传播量反而上升约{int(abs(reduction_rate))}%"
            else:
                effect_evaluation = f"处置后观察期较短（{post_days}天），暂为下降{int(reduction_rate)}%，建议继续观察"
        elif pre_days > 0 and post_days == 0:
            effect_evaluation = "刚刚处置，暂无后续传播数据，请稍后查看"
        else:
            effect_evaluation = "处置前数据不足，无法对比评估"

    return SpreadTrackResponse(
        rumor_id=rumor_id,
        title=rumor.title,
        intervention_time=rumor.intervention_time,
        trend=trend,
        effect_evaluation=effect_evaluation,
        intervention_stats=intervention_stats,
        observation_status=observation_status
    )


@router.post("/rumors/{rumor_id}/intervene")
def record_intervention(rumor_id: int, db: Session = Depends(get_db)):
    rumor = db.query(RumorCase).filter(RumorCase.id == rumor_id).first()
    if not rumor:
        raise HTTPException(status_code=404, detail="谣言案例不存在")

    rumor.intervention_time = datetime.now()
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

    return [_build_high_rumor(r) for r in rumors]


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

    export_data = {
        "export_date": date_obj.isoformat(),
        "total_high_risk": len(rumors),
        "export_time": datetime.now().isoformat(),
        "data": []
    }

    if group_by_category:
        groups = defaultdict(list)
        for r in rumors:
            groups[r.category].append(_build_high_rumor(r))

        export_data["by_category_count"] = {k: len(v) for k, v in groups.items()}
        export_data["groups"] = []

        for cat in settings.CATEGORIES:
            if cat in groups:
                cat_rumors = sorted(groups[cat], key=lambda x: x.risk_score, reverse=True)
                export_data["groups"].append({
                    "category": cat,
                    "count": len(cat_rumors),
                    "rumors": [
                        {
                            "rumor_id": r.rumor_id,
                            "title": r.title,
                            "risk_level": r.risk_level,
                            "risk_score": r.risk_score,
                            "total_shares": r.total_shares,
                            "main_channels": ", ".join(r.main_channels[:3]),
                            "handle_status": r.handle_status,
                            "debunk_status": r.debunk_status,
                            "recent_growth_rate": f"{int(r.recent_growth_rate * 100)}%" if r.recent_growth_rate else "N/A",
                            "first_seen": r.first_seen.isoformat(),
                            "last_active": r.last_active.isoformat()
                        }
                        for r in cat_rumors
                    ]
                })
    else:
        high_rumors = sorted([_build_high_rumor(r) for r in rumors], key=lambda x: x.risk_score, reverse=True)
        export_data["data"] = [
            {
                "rumor_id": r.rumor_id,
                "title": r.title,
                "category": r.category,
                "risk_level": r.risk_level,
                "risk_score": r.risk_score,
                "total_shares": r.total_shares,
                "main_channels": ", ".join(r.main_channels[:3]),
                "handle_status": r.handle_status,
                "debunk_status": r.debunk_status,
                "recent_growth_rate": f"{int(r.recent_growth_rate * 100)}%" if r.recent_growth_rate else "N/A",
                "affected_regions": ", ".join(r.affected_regions[:3]),
                "first_seen": r.first_seen.isoformat(),
                "last_active": r.last_active.isoformat()
            }
            for r in high_rumors
        ]

    if format == "json":
        filename = f"high_risk_rumors_{date_obj.isoformat()}.json"
        return JSONResponse(
            content=export_data,
            headers={
                "Content-Disposition": f"attachment; filename={filename}"
            }
        )
    else:
        filename = f"high_risk_rumors_{date_obj.isoformat()}.csv"

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "序号", "类别", "标题", "风险等级", "风险分值",
            "总传播量", "主要通道", "处置状态", "辟谣状态",
            "近期增长率", "受影响地区", "首次出现", "最后活跃"
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
            headers={
                "Content-Disposition": f"attachment; filename={filename}"
            }
        )
