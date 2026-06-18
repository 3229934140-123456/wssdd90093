from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
from typing import List
from database import get_db
from schemas import AuditQueryCreate, AuditQueryResponse
from models import AuditQuery
from analysis_engine import RumorAnalyzer

router = APIRouter(prefix="/audit", tags=["审核工单"])


@router.post("/analyze", response_model=AuditQueryResponse)
def analyze_rumor(query: AuditQueryCreate, db: Session = Depends(get_db)):
    existing = db.query(AuditQuery).filter(
        AuditQuery.ticket_id == query.ticket_id,
        AuditQuery.status == "completed"
    ).first()

    if existing:
        return AuditQueryResponse(
            query_id=existing.id,
            ticket_id=existing.ticket_id,
            status=existing.status,
            result=existing.result,
            created_at=existing.created_at,
            completed_at=existing.completed_at
        )

    audit_query = AuditQuery(
        ticket_id=query.ticket_id,
        text_content=query.text_content,
        topic_tags=query.topic_tags,
        content_url=query.content_url,
        submitter=query.submitter,
        status="processing"
    )
    db.add(audit_query)
    db.flush()

    analyzer = RumorAnalyzer(db)
    result, rumor_case = analyzer.analyze(
        query.text_content,
        query.topic_tags,
        query.content_url
    )

    audit_query.status = "completed"
    audit_query.result = result.model_dump(mode='json')
    audit_query.completed_at = datetime.utcnow()
    audit_query.rumor_case_id = rumor_case.id

    db.commit()
    db.refresh(audit_query)

    return AuditQueryResponse(
        query_id=audit_query.id,
        ticket_id=audit_query.ticket_id,
        status=audit_query.status,
        result=result,
        created_at=audit_query.created_at,
        completed_at=audit_query.completed_at
    )


@router.get("/queries", response_model=List[AuditQueryResponse])
def list_queries(submitter: str = None, status: str = None,
                 skip: int = 0, limit: int = 100,
                 db: Session = Depends(get_db)):
    q = db.query(AuditQuery)
    if submitter:
        q = q.filter(AuditQuery.submitter == submitter)
    if status:
        q = q.filter(AuditQuery.status == status)

    queries = q.order_by(AuditQuery.created_at.desc()).offset(skip).limit(limit).all()

    return [
        AuditQueryResponse(
            query_id=aq.id,
            ticket_id=aq.ticket_id,
            status=aq.status,
            result=aq.result,
            created_at=aq.created_at,
            completed_at=aq.completed_at
        )
        for aq in queries
    ]


@router.get("/queries/{query_id}", response_model=AuditQueryResponse)
def get_query(query_id: int, db: Session = Depends(get_db)):
    aq = db.query(AuditQuery).filter(AuditQuery.id == query_id).first()
    if not aq:
        raise HTTPException(status_code=404, detail="查询记录不存在")

    return AuditQueryResponse(
        query_id=aq.id,
        ticket_id=aq.ticket_id,
        status=aq.status,
        result=aq.result,
        created_at=aq.created_at,
        completed_at=aq.completed_at
    )
