import hashlib
import re
from datetime import datetime, timedelta
from typing import List, Optional, Tuple, Dict
from sqlalchemy.orm import Session
from config import settings
from models import RumorCase, SpreadRecord, DuplicateAccount, DebunkRecord
from schemas import (
    SourceInfo, DiffusionChannel, RiskAssessment,
    ActionableTip, DebunkInfo, AnalysisResult
)
import random


class RumorAnalyzer:
    def __init__(self, db: Session):
        self.db = db

    def analyze(self, text_content: Optional[str], topic_tags: Optional[List[str]],
                content_url: Optional[str]) -> Tuple[AnalysisResult, RumorCase]:
        content_hash = self._generate_content_hash(text_content, topic_tags, content_url)

        existing_case = self.db.query(RumorCase).filter(
            RumorCase.content_hash == content_hash
        ).first()

        if existing_case:
            return self._build_result_from_existing(existing_case), existing_case

        return self._analyze_new_content(text_content, topic_tags, content_url, content_hash)

    def _generate_content_hash(self, text_content: Optional[str],
                               topic_tags: Optional[List[str]],
                               content_url: Optional[str]) -> str:
        combined = ""
        if text_content:
            combined += self._normalize_text(text_content)
        if topic_tags:
            combined += "|".join(sorted(topic_tags))
        if content_url:
            combined += content_url
        return hashlib.md5(combined.encode('utf-8')).hexdigest()

    def _normalize_text(self, text: str) -> str:
        text = re.sub(r'[^\w\s]', '', text)
        text = re.sub(r'\s+', ' ', text)
        return text.lower().strip()

    def _analyze_new_content(self, text_content: Optional[str],
                             topic_tags: Optional[List[str]],
                             content_url: Optional[str],
                             content_hash: str) -> Tuple[AnalysisResult, RumorCase]:
        earliest_source = self._trace_earliest_source(text_content, topic_tags, content_url)
        main_channels = self._identify_diffusion_channels(text_content, topic_tags)
        risk_assessment = self._assess_risk(text_content, topic_tags, main_channels)
        actionable_tips = self._generate_actionable_tips(main_channels, risk_assessment)
        debunk_info = self._check_debunk_info(text_content, topic_tags)

        similar_cases = self.db.query(RumorCase).filter(
            RumorCase.category == risk_assessment.category
        ).count()

        now = datetime.utcnow()

        rumor_case = RumorCase(
            title=self._extract_title(text_content, topic_tags),
            content_hash=content_hash,
            category=risk_assessment.category,
            risk_level=risk_assessment.risk_level,
            risk_score=risk_assessment.risk_score,
            first_seen=earliest_source.publish_time if earliest_source else now,
            last_active=now,
            total_shares=sum(c.share_count for c in main_channels),
            affected_regions=list(set(c.region for c in main_channels if c.region)),
            debunk_status="已辟谣" if debunk_info.exists else "未辟谣",
            handle_status="待处理",
            earliest_source_url=earliest_source.source_url if earliest_source else None,
            earliest_source_platform=earliest_source.platform if earliest_source else None,
            earliest_source_author=earliest_source.author if earliest_source else None,
            main_channels=[c.model_dump() for c in main_channels],
            debunk_url=debunk_info.debunk_url if debunk_info.exists else None,
            debunk_authority=debunk_info.debunk_authority if debunk_info.exists else None,
            debunk_coverage=debunk_info.coverage_ratio if debunk_info.exists else None
        )

        self.db.add(rumor_case)
        self.db.flush()

        self._generate_mock_spread_records(rumor_case.id, main_channels)
        self._generate_mock_duplicate_accounts(rumor_case.id, text_content)

        if debunk_info.exists:
            debunk_record = DebunkRecord(
                rumor_case_id=rumor_case.id,
                debunk_url=debunk_info.debunk_url,
                authority=debunk_info.debunk_authority,
                publish_time=now - timedelta(hours=random.randint(1, 72)),
                view_count=random.randint(100, 10000),
                share_count=random.randint(10, 1000)
            )
            self.db.add(debunk_record)

        self.db.commit()

        result = AnalysisResult(
            earliest_source=earliest_source,
            main_channels=main_channels,
            risk_assessment=risk_assessment,
            actionable_tips=actionable_tips,
            debunk_info=debunk_info,
            similar_cases_count=similar_cases,
            analyzed_at=now
        )

        return result, rumor_case

    def _trace_earliest_source(self, text_content: Optional[str],
                               topic_tags: Optional[List[str]],
                               content_url: Optional[str]) -> Optional[SourceInfo]:
        platforms = ["微博", "微信公众号", "抖音", "快手", "小红书", "知乎", "B站", "豆瓣"]
        source_types = ["个人账号", "自媒体", "群组消息", "论坛帖子", "短视频"]

        if content_url:
            platform = self._extract_platform_from_url(content_url)
            return SourceInfo(
                source_url=content_url,
                source_type=random.choice(source_types),
                publish_time=datetime.utcnow() - timedelta(hours=random.randint(2, 168)),
                author=f"用户{random.randint(1000, 9999)}",
                platform=platform,
                confidence=round(random.uniform(0.7, 0.95), 2)
            )

        return SourceInfo(
            source_url=f"https://{random.choice(['weibo.com', 'www.zhihu.com', 'www.douyin.com'])}/status/{random.randint(100000, 999999)}",
            source_type=random.choice(source_types),
            publish_time=datetime.utcnow() - timedelta(hours=random.randint(12, 336)),
            author=f"用户{random.randint(1000, 9999)}",
            platform=random.choice(platforms),
            confidence=round(random.uniform(0.6, 0.85), 2)
        )

    def _extract_platform_from_url(self, url: str) -> str:
        if 'weibo' in url:
            return '微博'
        elif 'weixin' in url or 'mp.weixin' in url:
            return '微信公众号'
        elif 'douyin' in url or 'iesdouyin' in url:
            return '抖音'
        elif 'kuaishou' in url:
            return '快手'
        elif 'xiaohongshu' in url or 'xhslink' in url:
            return '小红书'
        elif 'zhihu' in url:
            return '知乎'
        elif 'bilibili' in url:
            return 'B站'
        elif 'douban' in url:
            return '豆瓣'
        else:
            return '其他平台'

    def _identify_diffusion_channels(self, text_content: Optional[str],
                                     topic_tags: Optional[List[str]]) -> List[DiffusionChannel]:
        channels = []
        regions = ["北京", "上海", "广东", "浙江", "江苏", "四川", "湖北", "河南", "山东", "河北"]

        num_channels = random.randint(2, 4)
        selected_channels = random.sample(settings.DIFFUSION_CHANNELS, num_channels)

        for i, channel_name in enumerate(selected_channels):
            share_count = random.randint(50, 5000)
            growth_rate = round(random.uniform(-0.1, 2.5), 2)
            is_rapid = growth_rate > 0.5 or share_count > 2000

            channels.append(DiffusionChannel(
                channel_name=channel_name,
                share_count=share_count,
                growth_rate=growth_rate,
                region=random.choice(regions) if i == 0 else None,
                is_rapid_growth=is_rapid
            ))

        return sorted(channels, key=lambda x: x.share_count, reverse=True)

    def _assess_risk(self, text_content: Optional[str],
                     topic_tags: Optional[List[str]],
                     channels: List[DiffusionChannel]) -> RiskAssessment:
        category = self._classify_category(text_content, topic_tags)

        base_score = random.randint(30, 85)

        high_risk_keywords = {
            "医疗健康": ["致癌", "死亡", "病毒", "疫情", "疫苗", "特效药"],
            "公共安全": ["爆炸", "火灾", "地震", "洪水", "袭击", "暴乱"],
            "民生政策": ["拆迁", "补贴", "退休", "社保", "房价", "油价"],
            "财经金融": ["暴跌", "崩盘", "诈骗", "传销", "非法集资", "银行破产"],
        }

        text = (text_content or "").lower()
        for cat, keywords in high_risk_keywords.items():
            if category == cat:
                for kw in keywords:
                    if kw in text:
                        base_score += random.randint(5, 15)

        total_shares = sum(c.share_count for c in channels)
        rapid_growth_count = sum(1 for c in channels if c.is_rapid_growth)

        if total_shares > 10000:
            base_score += 15
        elif total_shares > 5000:
            base_score += 10
        elif total_shares > 1000:
            base_score += 5

        base_score += rapid_growth_count * 8

        risk_score = min(100, max(0, base_score))

        if risk_score >= settings.HIGH_RISK_THRESHOLD + 15:
            risk_level = "极高"
        elif risk_score >= settings.HIGH_RISK_THRESHOLD:
            risk_level = "高"
        elif risk_score >= settings.MEDIUM_RISK_THRESHOLD:
            risk_level = "中"
        else:
            risk_level = "低"

        key_factors = self._extract_risk_factors(channels, risk_score, text)

        return RiskAssessment(
            risk_level=risk_level,
            risk_score=risk_score,
            category=category,
            key_factors=key_factors
        )

    def _classify_category(self, text_content: Optional[str],
                           topic_tags: Optional[List[str]]) -> str:
        text = (text_content or "").lower()
        tags = [t.lower() for t in (topic_tags or [])]

        category_keywords = {
            "医疗健康": ["医院", "医生", "药品", "疫苗", "病毒", "癌症", "健康", "中医", "西医", "疫情"],
            "公共安全": ["安全", "事故", "火灾", "爆炸", "地震", "警察", "犯罪", "恐怖", "袭击"],
            "民生政策": ["政策", "政府", "补贴", "社保", "退休", "房价", "教育", "高考", "拆迁"],
            "财经金融": ["股票", "基金", "投资", "理财", "银行", "贷款", "诈骗", "传销", "房价", "物价"],
            "教育文化": ["学校", "老师", "学生", "高考", "教育", "文化", "历史", "考试"],
        }

        scores = {}
        for cat, keywords in category_keywords.items():
            score = 0
            for kw in keywords:
                if kw in text:
                    score += 2
                for tag in tags:
                    if kw in tag:
                        score += 3
            scores[cat] = score

        max_cat = max(scores, key=scores.get)
        if scores[max_cat] > 0:
            return max_cat
        return "其他"

    def _extract_risk_factors(self, channels: List[DiffusionChannel],
                              risk_score: int, text: str) -> List[str]:
        factors = []
        total_shares = sum(c.share_count for c in channels)

        if total_shares > 5000:
            factors.append(f"累计传播量较大（{total_shares}次转发）")

        rapid_channels = [c for c in channels if c.is_rapid_growth]
        if rapid_channels:
            channel_names = "、".join(c.channel_name for c in rapid_channels[:2])
            factors.append(f"{channel_names}等渠道传播增速快")

        regions = set(c.region for c in channels if c.region)
        if len(regions) >= 3:
            factors.append(f"已扩散至{len(regions)}个地区")

        if risk_score >= 70:
            factors.append("内容涉及敏感领域，易引发公众恐慌")

        if not factors:
            factors.append("传播范围有限，暂未形成大规模扩散")

        return factors

    def _generate_actionable_tips(self, channels: List[DiffusionChannel],
                                  risk: RiskAssessment) -> List[ActionableTip]:
        tips = []
        total_shares = sum(c.share_count for c in channels)

        if total_shares > 1000:
            tips.append(ActionableTip(
                tip_type="duplicate_content",
                content=f"检测到 {random.randint(5, 50)} 个账号正在复用相同文案",
                severity="warning" if total_shares < 5000 else "danger",
                suggestion="建议批量标记相似内容，查看复用账号清单"
            ))

        rapid_channels = [c for c in channels if c.is_rapid_growth and c.region]
        for rc in rapid_channels[:1]:
            tips.append(ActionableTip(
                tip_type="regional_spike",
                content=f"{rc.region}地区{rc.channel_name}转发量突然增多（24小时增长{int(rc.growth_rate * 100)}%）",
                severity="danger",
                suggestion=f"建议重点监控{rc.region}地区相关群组，必要时协调当地资源"
            ))

        if risk.risk_level in ["高", "极高"]:
            tips.append(ActionableTip(
                tip_type="high_risk",
                content=f"当前风险等级为「{risk.risk_level}」，{risk.category}类内容",
                severity="danger",
                suggestion="建议立即升级处理，2小时内完成处置并记录"
            ))

        if random.random() > 0.5:
            tips.append(ActionableTip(
                tip_type="debunk_coverage",
                content="权威辟谣已出现但覆盖不足，辟谣内容触达率仅约30%",
                severity="warning",
                suggestion="建议推送辟谣内容至相关用户，协调官方账号转发扩大覆盖"
            ))

        return tips

    def _check_debunk_info(self, text_content: Optional[str],
                           topic_tags: Optional[List[str]]) -> DebunkInfo:
        has_debunk = random.random() > 0.6

        if has_debunk:
            authorities = ["央视新闻", "人民日报", "新华社", "国家卫健委", "中国互联网联合辟谣平台"]
            authority = random.choice(authorities)
            coverage = round(random.uniform(0.1, 0.6), 2)

            return DebunkInfo(
                exists=True,
                debunk_url=f"https://www.example.com/debunk/{random.randint(10000, 99999)}",
                debunk_authority=authority,
                coverage_ratio=coverage
            )

        return DebunkInfo(exists=False)

    def _extract_title(self, text_content: Optional[str],
                       topic_tags: Optional[List[str]]) -> str:
        if text_content:
            clean = re.sub(r'[^\w\s\u4e00-\u9fa5]', '', text_content)
            clean = clean.strip()
            if len(clean) > 30:
                return clean[:27] + "..."
            return clean if clean else "未命名谣言"
        if topic_tags:
            return "、".join(topic_tags[:3])
        return "未命名谣言"

    def _build_result_from_existing(self, case: RumorCase) -> AnalysisResult:
        now = datetime.utcnow()

        earliest_source = None
        if case.earliest_source_url:
            earliest_source = SourceInfo(
                source_url=case.earliest_source_url,
                source_type="历史记录",
                publish_time=case.first_seen,
                author=case.earliest_source_author,
                platform=case.earliest_source_platform or "未知",
                confidence=0.95
            )

        main_channels = [DiffusionChannel(**c) for c in (case.main_channels or [])]

        risk_assessment = RiskAssessment(
            risk_level=case.risk_level,
            risk_score=case.risk_score,
            category=case.category,
            key_factors=["已有历史分析记录"]
        )

        actionable_tips = [
            ActionableTip(
                tip_type="history",
                content=f"该谣言已在系统中存在，此前已有 {len(case.audit_queries)} 次查询",
                severity="info",
                suggestion="查看历史处置记录，参考已有处理方案"
            )
        ]

        debunk_info = DebunkInfo(
            exists=case.debunk_url is not None,
            debunk_url=case.debunk_url,
            debunk_authority=case.debunk_authority,
            coverage_ratio=case.debunk_coverage
        )

        similar_cases = self.db.query(RumorCase).filter(
            RumorCase.category == case.category,
            RumorCase.id != case.id
        ).count()

        return AnalysisResult(
            earliest_source=earliest_source,
            main_channels=main_channels,
            risk_assessment=risk_assessment,
            actionable_tips=actionable_tips,
            debunk_info=debunk_info,
            similar_cases_count=similar_cases,
            analyzed_at=now
        )

    def _generate_mock_spread_records(self, rumor_case_id: int, channels: List[DiffusionChannel]):
        now = datetime.utcnow()
        for i in range(14):
            timestamp = now - timedelta(days=13 - i)
            for channel in channels[:2]:
                base_share = int(channel.share_count / 14)
                variation = random.randint(-int(base_share * 0.3), int(base_share * 0.5))
                after_intervention = i >= 10 and random.random() > 0.5

                record = SpreadRecord(
                    rumor_case_id=rumor_case_id,
                    timestamp=timestamp,
                    share_count=max(0, base_share + variation),
                    channel=channel.channel_name,
                    region=channel.region,
                    after_intervention=after_intervention
                )
                self.db.add(record)

    def _generate_mock_duplicate_accounts(self, rumor_case_id: int, text_content: Optional[str]):
        platforms = ["微博", "微信公众号", "抖音", "小红书"]
        num_accounts = random.randint(3, 15)

        for _ in range(num_accounts):
            now = datetime.utcnow()
            account = DuplicateAccount(
                rumor_case_id=rumor_case_id,
                account_id=f"acc_{random.randint(100000, 999999)}",
                account_name=f"{'正能量' if random.random() > 0.5 else '生活'}{random.randint(1, 999)}",
                platform=random.choice(platforms),
                post_count=random.randint(1, 10),
                first_post_time=now - timedelta(hours=random.randint(12, 168)),
                last_post_time=now - timedelta(hours=random.randint(1, 48))
            )
            self.db.add(account)
