import pytest
from datetime import datetime
from analysis_engine import RumorAnalyzer
from models import RumorCase
from config import settings


class TestRumorAnalyzer:
    def test_generate_content_hash(self, db_session):
        analyzer = RumorAnalyzer(db_session)

        hash1 = analyzer._generate_content_hash("测试文本", ["标签1", "标签2"], None)
        hash2 = analyzer._generate_content_hash("测试文本", ["标签2", "标签1"], None)

        assert hash1 == hash2
        assert len(hash1) == 32

    def test_normalize_text(self, db_session):
        analyzer = RumorAnalyzer(db_session)

        result = analyzer._normalize_text("Hello,  World!  TEST")
        assert result == "hello world test"

        result = analyzer._normalize_text("你好，世界！")
        assert result == "你好世界"

    def test_classify_category_medical(self, db_session):
        analyzer = RumorAnalyzer(db_session)

        category = analyzer._classify_category("某药品致癌，请大家注意", ["健康", "药品"])
        assert category == "医疗健康"

    def test_classify_category_public_safety(self, db_session):
        analyzer = RumorAnalyzer(db_session)

        category = analyzer._classify_category("某地发生爆炸事件", ["安全", "事故"])
        assert category == "公共安全"

    def test_classify_category_other(self, db_session):
        analyzer = RumorAnalyzer(db_session)

        category = analyzer._classify_category("今天天气真好", ["天气"])
        assert category == "其他"

    def test_extract_platform_from_url(self, db_session):
        analyzer = RumorAnalyzer(db_session)

        assert analyzer._extract_platform_from_url("https://weibo.com/123") == "微博"
        assert analyzer._extract_platform_from_url("https://www.douyin.com/123") == "抖音"
        assert analyzer._extract_platform_from_url("https://www.zhihu.com/123") == "知乎"
        assert analyzer._extract_platform_from_url("https://example.com/123") == "其他平台"

    def test_analyze_new_content(self, db_session):
        analyzer = RumorAnalyzer(db_session)

        result, rumor_case = analyzer.analyze(
            "某地发生疫情病毒扩散，大家注意安全",
            ["疫情", "病毒"],
            None
        )

        assert result is not None
        assert rumor_case is not None
        assert result.earliest_source is not None
        assert len(result.main_channels) >= 2
        assert result.risk_assessment is not None
        assert result.risk_assessment.risk_level in settings.RISK_LEVELS
        assert len(result.actionable_tips) >= 1
        assert result.analyzed_at is not None

        assert rumor_case.title is not None
        assert rumor_case.category in settings.CATEGORIES
        assert rumor_case.risk_score >= 0
        assert rumor_case.risk_score <= 100

    def test_analyze_duplicate_content(self, db_session):
        analyzer = RumorAnalyzer(db_session)

        text = "重复内容测试文本"
        result1, case1 = analyzer.analyze(text, None, None)
        result2, case2 = analyzer.analyze(text, None, None)

        assert case1.id == case2.id
        assert "已有历史分析记录" in result2.risk_assessment.key_factors

    def test_assess_risk(self, db_session):
        analyzer = RumorAnalyzer(db_session)

        channels = analyzer._identify_diffusion_channels("测试", ["标签"])
        risk = analyzer._assess_risk("某病毒疫情扩散", ["疫情"], channels)

        assert risk.category == "医疗健康"
        assert risk.risk_level in settings.RISK_LEVELS
        assert 0 <= risk.risk_score <= 100
        assert len(risk.key_factors) >= 1

    def test_extract_title(self, db_session):
        analyzer = RumorAnalyzer(db_session)

        title = analyzer._extract_title("这是一段非常长的测试文本内容，专门用于测试标题提取功能是否能够正常的按照预期工作", None)
        assert len(title) <= 30
        assert title.endswith("...")

        title = analyzer._extract_title("短文本", None)
        assert title == "短文本"

        title = analyzer._extract_title(None, ["标签1", "标签2", "标签3", "标签4"])
        assert "标签1" in title
        assert "标签2" in title
        assert "标签3" in title

    def test_generate_actionable_tips_high_risk(self, db_session):
        analyzer = RumorAnalyzer(db_session)

        from schemas import DiffusionChannel, RiskAssessment, DebunkInfo

        channels = [
            DiffusionChannel(
                channel_name="社交媒体群组",
                share_count=3000,
                growth_rate=1.5,
                region="北京",
                is_rapid_growth=True
            )
        ]

        risk = RiskAssessment(
            risk_level="高",
            risk_score=85,
            category="医疗健康",
            key_factors=["传播量大"]
        )

        debunk_info = DebunkInfo(exists=False)
        duplicate_count = 8

        tips = analyzer._generate_actionable_tips(channels, risk, debunk_info, duplicate_count)

        tip_types = [t.tip_type for t in tips]
        assert "duplicate_content" in tip_types
        assert "high_risk" in tip_types
        assert "regional_spike" in tip_types

    def test_actionable_tips_stable_with_data(self, db_session):
        analyzer = RumorAnalyzer(db_session)

        from schemas import DiffusionChannel, RiskAssessment, DebunkInfo

        channels = [
            DiffusionChannel(
                channel_name="社交媒体群组",
                share_count=500,
                growth_rate=0.2,
                region="上海",
                is_rapid_growth=False
            )
        ]

        risk = RiskAssessment(
            risk_level="中",
            risk_score=50,
            category="其他",
            key_factors=[]
        )

        debunk_info = DebunkInfo(
            exists=True,
            debunk_url="https://example.com/debunk",
            debunk_authority="央视新闻",
            coverage_ratio=0.35
        )
        duplicate_count = 2

        tips = analyzer._generate_actionable_tips(channels, risk, debunk_info, duplicate_count)

        tip_types = [t.tip_type for t in tips]
        assert "duplicate_content" not in tip_types
        assert "regional_spike" not in tip_types
        assert "high_risk" not in tip_types
        assert "debunk_coverage" in tip_types

        debunk_tip = [t for t in tips if t.tip_type == "debunk_coverage"][0]
        assert "35%" in debunk_tip.content
        assert "央视新闻" in debunk_tip.content

    def test_calculate_duplicate_count(self, db_session):
        analyzer = RumorAnalyzer(db_session)

        from schemas import DiffusionChannel

        channels_small = [
            DiffusionChannel(
                channel_name="社交媒体群组",
                share_count=50,
                growth_rate=0.1,
                region=None,
                is_rapid_growth=False
            )
        ]
        count_small = analyzer._calculate_duplicate_account_count(None, channels_small)
        assert count_small >= 1

        channels_large = [
            DiffusionChannel(
                channel_name="社交媒体群组",
                share_count=8000,
                growth_rate=2.0,
                region="北京",
                is_rapid_growth=True
            ),
            DiffusionChannel(
                channel_name="短视频平台",
                share_count=6000,
                growth_rate=1.8,
                region=None,
                is_rapid_growth=True
            )
        ]
        count_large = analyzer._calculate_duplicate_account_count(None, channels_large)
        assert count_large >= 10
        assert count_large > count_small

    def test_duplicate_count_matches_accounts(self, db_session):
        analyzer = RumorAnalyzer(db_session)

        text = "测试文案内容"
        result, case = analyzer.analyze(text, ["标签1"], None)

        tip_types = [t.tip_type for t in result.actionable_tips]
        if "duplicate_content" in tip_types:
            dup_tip = [t for t in result.actionable_tips if t.tip_type == "duplicate_content"][0]
            import re
            match = re.search(r'(\d+)\s*个账号', dup_tip.content)
            if match:
                tip_count = int(match.group(1))
                db_count = len(case.duplicate_accounts)
                assert tip_count == db_count

    def test_check_debunk_info(self, db_session):
        analyzer = RumorAnalyzer(db_session)

        results = set()
        for _ in range(20):
            info = analyzer._check_debunk_info("测试", ["标签"])
            results.add(info.exists)

        assert True in results
        assert False in results

        for _ in range(10):
            info = analyzer._check_debunk_info("测试", ["标签"])
            if info.exists:
                assert info.debunk_url is not None
                assert info.debunk_authority is not None
                assert info.coverage_ratio is not None
                assert 0 <= info.coverage_ratio <= 1
                break
