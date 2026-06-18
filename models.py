from sqlalchemy import Column, Integer, String, DateTime, Float, Boolean, ForeignKey, Text, JSON
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base


class AuditQuery(Base):
    __tablename__ = "audit_queries"

    id = Column(Integer, primary_key=True, index=True)
    ticket_id = Column(String(64), index=True, nullable=False)
    text_content = Column(Text, nullable=True)
    topic_tags = Column(JSON, nullable=True)
    content_url = Column(String(512), nullable=True)
    submitter = Column(String(64), nullable=False)
    status = Column(String(32), default="pending")
    result = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    rumor_case_id = Column(Integer, ForeignKey("rumor_cases.id"), nullable=True)
    rumor_case = relationship("RumorCase", back_populates="audit_queries")


class RumorCase(Base):
    __tablename__ = "rumor_cases"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(256), nullable=False)
    content_hash = Column(String(128), unique=True, index=True, nullable=False)
    category = Column(String(64), nullable=False)
    risk_level = Column(String(32), nullable=False)
    risk_score = Column(Integer, nullable=False)
    first_seen = Column(DateTime, nullable=False)
    last_active = Column(DateTime, nullable=False)
    total_shares = Column(Integer, default=0)
    affected_regions = Column(JSON, default=list)
    debunk_status = Column(String(32), default="未辟谣")
    handle_status = Column(String(32), default="待处理")
    handle_stage = Column(String(32), default="待处置")
    earliest_source_url = Column(String(512), nullable=True)
    earliest_source_platform = Column(String(128), nullable=True)
    earliest_source_author = Column(String(128), nullable=True)
    main_channels = Column(JSON, default=list)
    debunk_url = Column(String(512), nullable=True)
    debunk_authority = Column(String(256), nullable=True)
    debunk_coverage = Column(Float, nullable=True)
    intervention_time = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    audit_queries = relationship("AuditQuery", back_populates="rumor_case")
    spread_records = relationship("SpreadRecord", back_populates="rumor_case")
    duplicate_accounts = relationship("DuplicateAccount", back_populates="rumor_case")


class SpreadRecord(Base):
    __tablename__ = "spread_records"

    id = Column(Integer, primary_key=True, index=True)
    rumor_case_id = Column(Integer, ForeignKey("rumor_cases.id"), nullable=False)
    timestamp = Column(DateTime, nullable=False)
    share_count = Column(Integer, nullable=False)
    channel = Column(String(128), nullable=False)
    region = Column(String(128), nullable=True)
    after_intervention = Column(Boolean, default=False)

    rumor_case = relationship("RumorCase", back_populates="spread_records")


class DuplicateAccount(Base):
    __tablename__ = "duplicate_accounts"

    id = Column(Integer, primary_key=True, index=True)
    rumor_case_id = Column(Integer, ForeignKey("rumor_cases.id"), nullable=False)
    account_id = Column(String(128), nullable=False)
    account_name = Column(String(256), nullable=False)
    platform = Column(String(128), nullable=False)
    post_count = Column(Integer, default=1)
    first_post_time = Column(DateTime, nullable=False)
    last_post_time = Column(DateTime, nullable=False)

    rumor_case = relationship("RumorCase", back_populates="duplicate_accounts")


class TipFeedback(Base):
    __tablename__ = "tip_feedbacks"

    id = Column(Integer, primary_key=True, index=True)
    query_id = Column(Integer, ForeignKey("audit_queries.id"), nullable=False)
    tip_type = Column(String(64), nullable=False)
    feedback = Column(String(32), nullable=False)
    submitter = Column(String(64), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class DebunkRecord(Base):
    __tablename__ = "debunk_records"

    id = Column(Integer, primary_key=True, index=True)
    rumor_case_id = Column(Integer, nullable=False)
    debunk_url = Column(String(512), nullable=False)
    authority = Column(String(256), nullable=False)
    publish_time = Column(DateTime, nullable=False)
    view_count = Column(Integer, default=0)
    share_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
