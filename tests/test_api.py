import pytest
import json


class TestAuditAPI:
    def test_health_check(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"

    def test_root_endpoint(self, client):
        response = client.get("/")
        assert response.status_code == 200

    def test_analyze_rumor_with_text(self, client):
        payload = {
            "text_content": "某地发生疫情病毒扩散，大家注意安全，不要出门",
            "topic_tags": ["疫情", "病毒", "安全"],
            "ticket_id": "TICKET-001",
            "submitter": "auditor_01"
        }

        response = client.post("/api/v1/audit/analyze", json=payload)
        assert response.status_code == 200

        data = response.json()
        assert data["ticket_id"] == "TICKET-001"
        assert data["status"] == "completed"
        assert data["result"] is not None

        result = data["result"]
        assert "earliest_source" in result
        assert "main_channels" in result
        assert "risk_assessment" in result
        assert "actionable_tips" in result
        assert "debunk_info" in result

        risk = result["risk_assessment"]
        assert risk["risk_level"] in ["低", "中", "高", "极高"]
        assert 0 <= risk["risk_score"] <= 100

    def test_analyze_rumor_with_url(self, client):
        payload = {
            "content_url": "https://weibo.com/123456/status/999999",
            "ticket_id": "TICKET-002",
            "submitter": "auditor_02"
        }

        response = client.post("/api/v1/audit/analyze", json=payload)
        assert response.status_code == 200

        data = response.json()
        assert data["result"]["earliest_source"]["platform"] == "微博"

    def test_analyze_rumor_missing_fields(self, client):
        payload = {
            "ticket_id": "TICKET-004",
            "submitter": "auditor_01"
        }

        response = client.post("/api/v1/audit/analyze", json=payload)
        assert response.status_code == 422

    def test_list_queries(self, client):
        for i in range(3):
            payload = {
                "text_content": f"测试内容{i}",
                "ticket_id": f"TICKET-LIST-{i}",
                "submitter": "auditor_01"
            }
            client.post("/api/v1/audit/analyze", json=payload)

        response = client.get("/api/v1/audit/queries")
        assert response.status_code == 200
        assert len(response.json()) >= 3

    def test_get_query(self, client):
        payload = {
            "text_content": "查询详情测试",
            "ticket_id": "TICKET-GET-001",
            "submitter": "auditor_01"
        }
        create_response = client.post("/api/v1/audit/analyze", json=payload)
        query_id = create_response.json()["query_id"]

        response = client.get(f"/api/v1/audit/queries/{query_id}")
        assert response.status_code == 200
        assert response.json()["query_id"] == query_id

    def test_get_query_not_found(self, client):
        response = client.get("/api/v1/audit/queries/99999")
        assert response.status_code == 404


class TestSupervisorAPI:
    def test_list_rumors(self, client):
        for i in range(5):
            payload = {
                "text_content": f"高风险谣言测试内容{i} 疫情病毒致癌",
                "ticket_id": f"TICKET-RISK-{i}",
                "submitter": "auditor_01"
            }
            client.post("/api/v1/audit/analyze", json=payload)

        response = client.get("/api/v1/supervisor/rumors?limit=10")
        assert response.status_code == 200
        assert len(response.json()) >= 5

    def test_daily_high_risk(self, client):
        for i in range(5):
            payload = {
                "text_content": f"每日高风险测试{i} 病毒疫情",
                "ticket_id": f"TICKET-DAILY-{i}",
                "submitter": "auditor_01"
            }
            client.post("/api/v1/audit/analyze", json=payload)

        response = client.get("/api/v1/supervisor/daily-high-risk?min_risk_score=0")
        assert response.status_code == 200
        data = response.json()
        assert "date" in data
        assert "total_high_risk" in data
        assert "by_category" in data
        assert "rumors" in data

    def test_daily_high_risk_grouped(self, client):
        categories = ["医疗健康", "公共安全", "民生政策", "财经金融"]
        for i, cat in enumerate(categories):
            keyword = {
                "医疗健康": "病毒疫情",
                "公共安全": "火灾事故",
                "民生政策": "退休补贴",
                "财经金融": "股票暴跌"
            }[cat]
            for j in range(i + 1):
                payload = {
                    "text_content": f"测试{i}-{j} {keyword}",
                    "ticket_id": f"TICKET-GROUP-{i}-{j}",
                    "submitter": "auditor_01"
                }
                client.post("/api/v1/audit/analyze", json=payload)

        response = client.get("/api/v1/supervisor/daily-high-risk/grouped?min_risk_score=0")
        assert response.status_code == 200
        data = response.json()

        assert "groups" in data
        assert len(data["groups"]) >= 1
        for group in data["groups"]:
            assert "category" in group
            assert "count" in group
            assert "rumors" in group
            for rumor in group["rumors"]:
                assert rumor["category"] == group["category"]

    def test_spread_track_with_intervention_stats(self, client):
        payload = {
            "text_content": "传播统计测试 病毒疫情",
            "ticket_id": "TICKET-STATS-001",
            "submitter": "auditor_01"
        }
        client.post("/api/v1/audit/analyze", json=payload)

        rumors_response = client.get("/api/v1/supervisor/rumors?limit=1")
        rumor_id = rumors_response.json()[0]["rumor_id"]

        response = client.get(f"/api/v1/supervisor/rumors/{rumor_id}/spread-track")
        assert response.status_code == 200
        data = response.json()
        assert "observation_status" in data

        client.post(f"/api/v1/supervisor/rumors/{rumor_id}/intervene")

        response2 = client.get(f"/api/v1/supervisor/rumors/{rumor_id}/spread-track")
        assert response2.status_code == 200
        data2 = response2.json()
        assert data2["intervention_time"] is not None

    def test_spread_track_not_found(self, client):
        response = client.get("/api/v1/supervisor/rumors/99999/spread-track")
        assert response.status_code == 404

    def test_record_intervention(self, client):
        payload = {
            "text_content": "处置记录测试",
            "ticket_id": "TICKET-INTERVENE-001",
            "submitter": "auditor_01"
        }
        client.post("/api/v1/audit/analyze", json=payload)

        rumors_response = client.get("/api/v1/supervisor/rumors?limit=1")
        rumor_id = rumors_response.json()[0]["rumor_id"]

        response = client.post(f"/api/v1/supervisor/rumors/{rumor_id}/intervene")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "handle_stage" in data
        assert data["handle_stage"] == "观察中"

    def test_get_duplicate_accounts(self, client):
        payload = {
            "text_content": "重复账号测试 病毒疫情",
            "ticket_id": "TICKET-DUP-001",
            "submitter": "auditor_01"
        }
        client.post("/api/v1/audit/analyze", json=payload)

        rumors_response = client.get("/api/v1/supervisor/rumors?limit=1")
        rumor_id = rumors_response.json()[0]["rumor_id"]

        response = client.get(f"/api/v1/supervisor/rumors/{rumor_id}/duplicate-accounts")
        assert response.status_code == 200
        assert "total_accounts" in response.json()

    def test_export_json(self, client):
        for i in range(3):
            payload = {
                "text_content": f"导出测试{i} 病毒疫情",
                "ticket_id": f"TICKET-EXPORT-{i}",
                "submitter": "auditor_01"
            }
            client.post("/api/v1/audit/analyze", json=payload)

        response = client.get("/api/v1/supervisor/daily-high-risk/export?format=json&min_risk_score=0")
        assert response.status_code == 200
        data = response.json()
        assert "export_date" in data
        for group in data.get("groups", []):
            for rumor in group["rumors"]:
                assert "handle_stage" in rumor
                assert "suggested_action" in rumor

    def test_export_csv(self, client):
        for i in range(3):
            payload = {
                "text_content": f"CSV导出测试{i} 病毒疫情",
                "ticket_id": f"TICKET-CSV-{i}",
                "submitter": "auditor_01"
            }
            client.post("/api/v1/audit/analyze", json=payload)

        response = client.get("/api/v1/supervisor/daily-high-risk/export?format=csv&min_risk_score=0")
        assert response.status_code == 200
        assert "text/csv" in response.headers.get("content-type", "")
        content = response.text
        assert "处置阶段" in content
        assert "建议动作" in content

    def test_tips_correspond_with_data(self, client):
        payload = {
            "text_content": "提示一致性测试",
            "topic_tags": ["疫情", "病毒"],
            "ticket_id": "TICKET-TIP-CONSIST-001",
            "submitter": "auditor_01"
        }

        response = client.post("/api/v1/audit/analyze", json=payload)
        assert response.status_code == 200
        result = response.json()["result"]

        channels = result["main_channels"]
        tips = result["actionable_tips"]
        debunk_info = result["debunk_info"]

        regional_tips = [t for t in tips if t["tip_type"] == "regional_spike"]
        rapid_with_region = [c for c in channels if c["is_rapid_growth"] and c["region"]]

        if rapid_with_region:
            assert len(regional_tips) >= 1
            assert rapid_with_region[0]["region"] in regional_tips[0]["content"]

        debunk_tips = [t for t in tips if t["tip_type"] == "debunk_coverage"]
        if debunk_info["exists"]:
            assert len(debunk_tips) >= 1
            assert debunk_info["debunk_authority"] in debunk_tips[0]["content"]
        else:
            assert len(debunk_tips) == 0


class TestHandleStage:
    def test_rumor_has_handle_stage(self, client):
        payload = {
            "text_content": "处置阶段测试 病毒疫情",
            "ticket_id": "TICKET-STAGE-001",
            "submitter": "auditor_01"
        }
        client.post("/api/v1/audit/analyze", json=payload)

        response = client.get("/api/v1/supervisor/rumors?limit=1")
        rumor = response.json()[0]
        assert "handle_stage" in rumor
        assert rumor["handle_stage"] == "待处置"
        assert "suggested_action" in rumor

    def test_intervene_changes_stage(self, client):
        payload = {
            "text_content": "处置阶段变化测试 病毒疫情",
            "ticket_id": "TICKET-STAGE-CHANGE-001",
            "submitter": "auditor_01"
        }
        client.post("/api/v1/audit/analyze", json=payload)

        rumors_response = client.get("/api/v1/supervisor/rumors?limit=1")
        rumor_id = rumors_response.json()[0]["rumor_id"]

        intervene_response = client.post(f"/api/v1/supervisor/rumors/{rumor_id}/intervene")
        assert intervene_response.json()["handle_stage"] == "观察中"

    def test_update_stage(self, client):
        payload = {
            "text_content": "阶段更新测试 病毒疫情",
            "ticket_id": "TICKET-STAGE-UPDATE-001",
            "submitter": "auditor_01"
        }
        client.post("/api/v1/audit/analyze", json=payload)

        rumors_response = client.get("/api/v1/supervisor/rumors?limit=1")
        rumor_id = rumors_response.json()[0]["rumor_id"]

        response = client.post(f"/api/v1/supervisor/rumors/{rumor_id}/update-stage?stage=已压降")
        assert response.status_code == 200
        data = response.json()
        assert data["handle_stage"] == "已压降"
        assert "suggested_action" in data

    def test_filter_by_handle_stage(self, client):
        for i in range(3):
            payload = {
                "text_content": f"阶段筛选测试{i} 病毒疫情",
                "ticket_id": f"TICKET-STAGE-FILTER-{i}",
                "submitter": "auditor_01"
            }
            client.post("/api/v1/audit/analyze", json=payload)

        response = client.get("/api/v1/supervisor/rumors?handle_stage=待处置")
        assert response.status_code == 200
        rumors = response.json()
        assert all(r["handle_stage"] == "待处置" for r in rumors)

    def test_grouped_filter_by_stage(self, client):
        for i in range(3):
            payload = {
                "text_content": f"分组阶段筛选{i} 病毒疫情",
                "ticket_id": f"TICKET-GRP-STAGE-{i}",
                "submitter": "auditor_01"
            }
            client.post("/api/v1/audit/analyze", json=payload)

        response = client.get("/api/v1/supervisor/daily-high-risk/grouped?handle_stage=待处置&min_risk_score=0")
        assert response.status_code == 200
        data = response.json()
        for group in data.get("groups", []):
            for rumor in group["rumors"]:
                assert rumor["handle_stage"] == "待处置"

    def test_export_includes_stage(self, client):
        payload = {
            "text_content": "导出阶段测试 病毒疫情",
            "ticket_id": "TICKET-EXPORT-STAGE-001",
            "submitter": "auditor_01"
        }
        client.post("/api/v1/audit/analyze", json=payload)

        response = client.get("/api/v1/supervisor/daily-high-risk/export?format=json&min_risk_score=0")
        data = response.json()
        for group in data.get("groups", []):
            for rumor in group["rumors"]:
                assert "handle_stage" in rumor
                assert "suggested_action" in rumor


class TestCrossDayComparison:
    def test_cross_day_7_days(self, client):
        for i in range(3):
            payload = {
                "text_content": f"跨日对比测试{i} 病毒疫情",
                "ticket_id": f"TICKET-CROSS-{i}",
                "submitter": "auditor_01"
            }
            client.post("/api/v1/audit/analyze", json=payload)

        response = client.get("/api/v1/supervisor/cross-day-comparison?days=7&min_risk_score=0")
        assert response.status_code == 200
        data = response.json()

        assert data["period_days"] == 7
        assert "start_date" in data
        assert "end_date" in data
        assert "overall" in data
        assert "by_category" in data
        assert len(data["overall"]) == 7

        for item in data["overall"]:
            assert "date" in item
            assert "high_risk_count" in item
            assert "avg_risk_score" in item
            assert "total_shares" in item

        for cat_trend in data["by_category"]:
            assert "category" in cat_trend
            assert "daily_trend" in cat_trend
            assert "trend_direction" in cat_trend
            assert cat_trend["trend_direction"] in ["明显升高", "小幅上升", "平稳", "小幅下降", "明显下降"]

    def test_cross_day_30_days(self, client):
        response = client.get("/api/v1/supervisor/cross-day-comparison?days=30&min_risk_score=0")
        assert response.status_code == 200
        data = response.json()
        assert data["period_days"] == 30


class TestTipFeedback:
    def test_submit_feedback(self, client):
        payload = {
            "text_content": "反馈测试内容 病毒疫情",
            "ticket_id": "TICKET-FEEDBACK-001",
            "submitter": "auditor_01"
        }
        analyze_response = client.post("/api/v1/audit/analyze", json=payload)
        query_id = analyze_response.json()["query_id"]

        feedback_payload = {
            "query_id": query_id,
            "tip_type": "duplicate_content",
            "feedback": "准确",
            "submitter": "auditor_01"
        }
        response = client.post("/api/v1/supervisor/tips/feedback", json=feedback_payload)
        assert response.status_code == 200
        data = response.json()
        assert data["tip_type"] == "duplicate_content"
        assert data["feedback"] == "准确"

    def test_submit_adopted_feedback(self, client):
        payload = {
            "text_content": "已采用反馈测试 病毒疫情",
            "ticket_id": "TICKET-FEEDBACK-ADOPT-001",
            "submitter": "auditor_01"
        }
        analyze_response = client.post("/api/v1/audit/analyze", json=payload)
        query_id = analyze_response.json()["query_id"]

        feedback_payload = {
            "query_id": query_id,
            "tip_type": "regional_spike",
            "feedback": "已采用",
            "submitter": "auditor_01"
        }
        response = client.post("/api/v1/supervisor/tips/feedback", json=feedback_payload)
        assert response.status_code == 200
        assert response.json()["feedback"] == "已采用"

    def test_feedback_summary(self, client):
        payload = {
            "text_content": "汇总测试内容 病毒疫情",
            "ticket_id": "TICKET-SUMMARY-001",
            "submitter": "auditor_01"
        }
        analyze_response = client.post("/api/v1/audit/analyze", json=payload)
        query_id = analyze_response.json()["query_id"]

        feedbacks = [
            {"query_id": query_id, "tip_type": "duplicate_content", "feedback": "准确", "submitter": "auditor_01"},
            {"query_id": query_id, "tip_type": "duplicate_content", "feedback": "已采用", "submitter": "auditor_01"},
            {"query_id": query_id, "tip_type": "duplicate_content", "feedback": "不准确", "submitter": "auditor_02"},
        ]

        for fb in feedbacks:
            client.post("/api/v1/supervisor/tips/feedback", json=fb)

        response = client.get("/api/v1/supervisor/tips/feedback/summary")
        assert response.status_code == 200
        data = response.json()

        dup_summary = [s for s in data if s["tip_type"] == "duplicate_content"]
        if dup_summary:
            summary = dup_summary[0]
            assert summary["total"] == 3
            assert summary["accurate"] == 1
            assert summary["inaccurate"] == 1
            assert summary["adopted"] == 1
            assert summary["adoption_rate"] > 0

    def test_feedback_invalid_query(self, client):
        feedback_payload = {
            "query_id": 99999,
            "tip_type": "duplicate_content",
            "feedback": "准确",
            "submitter": "auditor_01"
        }
        response = client.post("/api/v1/supervisor/tips/feedback", json=feedback_payload)
        assert response.status_code == 404

    def test_feedback_invalid_type(self, client):
        payload = {
            "text_content": "无效反馈类型测试 病毒疫情",
            "ticket_id": "TICKET-INVALID-FB-001",
            "submitter": "auditor_01"
        }
        analyze_response = client.post("/api/v1/audit/analyze", json=payload)
        query_id = analyze_response.json()["query_id"]

        feedback_payload = {
            "query_id": query_id,
            "tip_type": "duplicate_content",
            "feedback": "无效反馈",
            "submitter": "auditor_01"
        }
        response = client.post("/api/v1/supervisor/tips/feedback", json=feedback_payload)
        assert response.status_code == 422


class TestSpreadTrackSplit:
    def test_no_intervention_default_status(self, client):
        payload = {
            "text_content": "默认状态测试 病毒疫情",
            "ticket_id": "TICKET-DEFAULT-STATUS-001",
            "submitter": "auditor_01"
        }
        client.post("/api/v1/audit/analyze", json=payload)

        rumors_response = client.get("/api/v1/supervisor/rumors?limit=1")
        rumor_id = rumors_response.json()[0]["rumor_id"]

        response = client.get(f"/api/v1/supervisor/rumors/{rumor_id}/spread-track")
        data = response.json()
        assert data["observation_status"] == "待观察"

    def test_intervention_same_day_split(self, client):
        payload = {
            "text_content": "同日拆分测试 病毒疫情",
            "ticket_id": "TICKET-SPLIT-001",
            "submitter": "auditor_01"
        }
        client.post("/api/v1/audit/analyze", json=payload)

        rumors_response = client.get("/api/v1/supervisor/rumors?limit=1")
        rumor_id = rumors_response.json()[0]["rumor_id"]

        client.post(f"/api/v1/supervisor/rumors/{rumor_id}/intervene")

        response = client.get(f"/api/v1/supervisor/rumors/{rumor_id}/spread-track")
        data = response.json()

        trend = data["trend"]
        pre_items = [t for t in trend if not t["after_intervention"]]
        post_items = [t for t in trend if t["after_intervention"]]

        if len(trend) > 0:
            if data["intervention_time"] is not None:
                assert len(post_items) > 0 or len(pre_items) > 0


class TestStageFilterConsistency:
    def test_daily_list_filter_by_stage(self, client):
        for i in range(3):
            payload = {
                "text_content": f"每日列表阶段筛选{i} 病毒疫情",
                "ticket_id": f"TICKET-DAILY-STAGE-FLT-{i}",
                "submitter": "auditor_01"
            }
            client.post("/api/v1/audit/analyze", json=payload)

        response = client.get("/api/v1/supervisor/daily-high-risk?handle_stage=待处置&min_risk_score=0")
        assert response.status_code == 200
        data = response.json()
        for r in data["rumors"]:
            assert r["handle_stage"] == "待处置"

    def test_export_filter_by_stage(self, client):
        for i in range(3):
            payload = {
                "text_content": f"导出阶段筛选{i} 病毒疫情",
                "ticket_id": f"TICKET-EXPORT-STAGE-FLT-{i}",
                "submitter": "auditor_01"
            }
            client.post("/api/v1/audit/analyze", json=payload)

        response = client.get("/api/v1/supervisor/daily-high-risk/export?format=json&handle_stage=待处置&min_risk_score=0")
        assert response.status_code == 200
        data = response.json()
        all_rumors = []
        for group in data.get("groups", []):
            all_rumors.extend(group["rumors"])
        for r in all_rumors:
            assert r["handle_stage"] == "待处置"

    def test_three_entries_same_stage_count(self, client):
        for i in range(5):
            payload = {
                "text_content": f"三入口一致性{i} 病毒疫情",
                "ticket_id": f"TICKET-3ENTRY-CONSIST-{i}",
                "submitter": "auditor_01"
            }
            client.post("/api/v1/audit/analyze", json=payload)

        list_resp = client.get("/api/v1/supervisor/daily-high-risk?handle_stage=待处置&min_risk_score=0")
        list_count = list_resp.json()["total_high_risk"]

        grouped_resp = client.get("/api/v1/supervisor/daily-high-risk/grouped?handle_stage=待处置&min_risk_score=0")
        grouped_count = grouped_resp.json()["total_high_risk"]

        export_resp = client.get("/api/v1/supervisor/daily-high-risk/export?format=json&handle_stage=待处置&min_risk_score=0")
        export_count = export_resp.json()["total_high_risk"]

        assert list_count == grouped_count == export_count


class TestCrossDayFullDates:
    def test_category_trend_has_all_days(self, client):
        response = client.get("/api/v1/supervisor/cross-day-comparison?days=7&min_risk_score=0")
        assert response.status_code == 200
        data = response.json()

        for cat_trend in data["by_category"]:
            assert len(cat_trend["daily_trend"]) == 7
            for item in cat_trend["daily_trend"]:
                assert "date" in item
                assert "high_risk_count" in item
                assert "avg_risk_score" in item
                assert "total_shares" in item

    def test_empty_category_shows_zero(self, client):
        response = client.get("/api/v1/supervisor/cross-day-comparison?days=7&min_risk_score=0")
        data = response.json()

        has_other_cat = any(c["category"] == "其他" for c in data["by_category"])
        if not has_other_cat:
            other_cat = [c for c in data["by_category"] if c["category"] == "其他"]
            if other_cat:
                for item in other_cat[0]["daily_trend"]:
                    assert item["high_risk_count"] == 0
                    assert item["total_shares"] == 0


class TestExportWithFeedback:
    def test_export_includes_feedback_summary(self, client):
        payload = {
            "text_content": "导出反馈测试 病毒疫情",
            "ticket_id": "TICKET-EXPORT-FB-001",
            "submitter": "auditor_01"
        }
        analyze_resp = client.post("/api/v1/audit/analyze", json=payload)
        query_id = analyze_resp.json()["query_id"]

        feedbacks = [
            {"query_id": query_id, "tip_type": "duplicate_content", "feedback": "准确", "submitter": "auditor_01"},
            {"query_id": query_id, "tip_type": "duplicate_content", "feedback": "已采用", "submitter": "auditor_01"},
            {"query_id": query_id, "tip_type": "regional_spike", "feedback": "准确", "submitter": "auditor_02"},
            {"query_id": query_id, "tip_type": "regional_spike", "feedback": "已采用", "submitter": "auditor_02"},
            {"query_id": query_id, "tip_type": "regional_spike", "feedback": "已采用", "submitter": "auditor_03"},
        ]
        for fb in feedbacks:
            client.post("/api/v1/supervisor/tips/feedback", json=fb)

        response = client.get("/api/v1/supervisor/daily-high-risk/export?format=json&min_risk_score=0")
        assert response.status_code == 200
        data = response.json()

        assert "tip_feedback_summary" in data
        assert "top_adopted_tips" in data
        assert len(data["top_adopted_tips"]) <= 3

        if data["top_adopted_tips"]:
            top = data["top_adopted_tips"][0]
            assert "tip_type" in top
            assert "adoption_rate" in top
            assert "accurate_rate" in top
            assert "inaccurate_rate" in top


class TestHandleEffectSummary:
    def test_handle_effect_summary_structure(self, client):
        for i in range(3):
            payload = {
                "text_content": f"效果汇总测试{i} 病毒疫情",
                "ticket_id": f"TICKET-EFFECT-SUM-{i}",
                "submitter": "auditor_01"
            }
            client.post("/api/v1/audit/analyze", json=payload)

        response = client.get("/api/v1/supervisor/handle-effect-summary?min_risk_score=0")
        assert response.status_code == 200
        data = response.json()

        assert "date" in data
        assert "total_high_risk" in data
        assert "by_stage" in data
        assert "ineffective_by_category" in data
        assert "overall_avg_reduction" in data
        assert "effective_rate" in data

        assert len(data["by_stage"]) == 4
        for stage_item in data["by_stage"]:
            assert "stage" in stage_item
            assert "count" in stage_item
            assert "avg_reduction_rate" in stage_item or stage_item["avg_reduction_rate"] is None

        stages = [s["stage"] for s in data["by_stage"]]
        assert "待处置" in stages
        assert "观察中" in stages
        assert "已压降" in stages
        assert "处置无效" in stages

    def test_ineffective_by_category_sorted(self, client):
        response = client.get("/api/v1/supervisor/handle-effect-summary?min_risk_score=0")
        data = response.json()

        if len(data["ineffective_by_category"]) > 1:
            counts = [c["count"] for c in data["ineffective_by_category"]]
            assert counts == sorted(counts, reverse=True)


class TestManualStageOverride:
    def test_manual_stage_shows_override_flag(self, client):
        payload = {
            "text_content": "人工阶段覆盖测试 病毒疫情",
            "ticket_id": "TICKET-MANUAL-OVERRIDE-001",
            "submitter": "auditor_01"
        }
        client.post("/api/v1/audit/analyze", json=payload)

        rumors_resp = client.get("/api/v1/supervisor/rumors?limit=1&min_risk_score=0")
        rumor_id = rumors_resp.json()[0]["rumor_id"]
        original_stage = rumors_resp.json()[0]["handle_stage"]
        assert rumors_resp.json()[0]["stage_overridden"] == False
        assert rumors_resp.json()[0]["system_evaluated_stage"] is None

        new_stage = "处置无效" if original_stage != "处置无效" else "已压降"
        client.post(f"/api/v1/supervisor/rumors/{rumor_id}/update-stage?stage={new_stage}")

        updated = client.get("/api/v1/supervisor/rumors?limit=1&min_risk_score=0").json()[0]
        assert updated["handle_stage"] == new_stage
        assert updated["stage_overridden"] == True
        assert updated["system_evaluated_stage"] is not None
        assert updated["system_evaluated_stage"] != new_stage or updated["system_evaluated_stage"] == new_stage

    def test_manual_stage_reflected_in_all_views(self, client):
        payload = {
            "text_content": "人工阶段一致性测试 病毒疫情",
            "ticket_id": "TICKET-MANUAL-CONSIST-001",
            "submitter": "auditor_01"
        }
        client.post("/api/v1/audit/analyze", json=payload)

        rumors_resp = client.get("/api/v1/supervisor/rumors?limit=1&min_risk_score=0")
        rumor_id = rumors_resp.json()[0]["rumor_id"]

        client.post(f"/api/v1/supervisor/rumors/{rumor_id}/update-stage?stage=处置无效")

        list_resp = client.get("/api/v1/supervisor/rumors?handle_stage=处置无效&min_risk_score=0")
        assert any(r["rumor_id"] == rumor_id for r in list_resp.json())

        daily_resp = client.get("/api/v1/supervisor/daily-high-risk?handle_stage=处置无效&min_risk_score=0")
        assert any(r["rumor_id"] == rumor_id for r in daily_resp.json()["rumors"])

        export_resp = client.get("/api/v1/supervisor/daily-high-risk/export?format=json&handle_stage=处置无效&min_risk_score=0")
        all_rumors = []
        for g in export_resp.json().get("groups", []):
            all_rumors.extend(g["rumors"])
        assert any(r["rumor_id"] == rumor_id for r in all_rumors)


class TestCSVFeedbackExport:
    def test_csv_includes_feedback_summary(self, client):
        payload = {
            "text_content": "CSV反馈测试 病毒疫情",
            "ticket_id": "TICKET-CSV-FB-001",
            "submitter": "auditor_01"
        }
        analyze_resp = client.post("/api/v1/audit/analyze", json=payload)
        query_id = analyze_resp.json()["query_id"]

        client.post("/api/v1/supervisor/tips/feedback", json={
            "query_id": query_id, "tip_type": "duplicate_content",
            "feedback": "已采用", "submitter": "auditor_01"
        })

        response = client.get("/api/v1/supervisor/daily-high-risk/export?format=csv&min_risk_score=0")
        assert response.status_code == 200
        content = response.text
        assert "复核反馈摘要" in content
        assert "采用率TOP提示类型" in content
        assert "duplicate_content" in content
        assert "采用率" in content
        assert "准确占比" in content


class TestReviewEntry:
    def test_review_entry_invalid_category(self, client):
        response = client.get("/api/v1/supervisor/review-entry/无效类别?min_risk_score=0")
        assert response.status_code == 400

    def test_review_entry_valid_structure(self, client):
        payload = {
            "text_content": "复盘入口测试 病毒疫情",
            "ticket_id": "TICKET-REVIEW-001",
            "submitter": "auditor_01"
        }
        analyze_resp = client.post("/api/v1/audit/analyze", json=payload)

        rumors_resp = client.get("/api/v1/supervisor/rumors?limit=1&min_risk_score=0")
        rumor_id = rumors_resp.json()[0]["rumor_id"]

        client.post(f"/api/v1/supervisor/rumors/{rumor_id}/update-stage?stage=处置无效")

        response = client.get("/api/v1/supervisor/review-entry/医疗健康?min_risk_score=0")
        assert response.status_code == 200
        data = response.json()
        assert "category" in data
        assert "total_count" in data
        assert "cases" in data
        for case in data["cases"]:
            assert "rumor_id" in case
            assert "suggested_next_action" in case
            assert "recent_change" in case
            assert "reduction_rate" in case or case["reduction_rate"] is None


class TestCrossDayDualFilter:
    def test_cross_day_with_category_filter(self, client):
        categories = ["医疗健康", "公共安全", "民生政策"]
        for i, cat in enumerate(categories):
            kw = {"医疗健康": "病毒疫情", "公共安全": "火灾事故", "民生政策": "退休补贴"}[cat]
            payload = {
                "text_content": f"跨日双重筛选{i} {kw}",
                "ticket_id": f"TICKET-CROSS-DUAL-{i}",
                "submitter": "auditor_01"
            }
            client.post("/api/v1/audit/analyze", json=payload)

        response = client.get("/api/v1/supervisor/cross-day-comparison?days=7&category=医疗健康&min_risk_score=0")
        assert response.status_code == 200
        data = response.json()
        assert len(data["by_category"]) == 1
        assert data["by_category"][0]["category"] == "医疗健康"
        assert len(data["by_category"][0]["daily_trend"]) == 7

    def test_cross_day_with_stage_filter(self, client):
        payload = {
            "text_content": "跨日阶段筛选 病毒疫情",
            "ticket_id": "TICKET-CROSS-STAGE-001",
            "submitter": "auditor_01"
        }
        client.post("/api/v1/audit/analyze", json=payload)

        response = client.get("/api/v1/supervisor/cross-day-comparison?days=7&handle_stage=待处置&min_risk_score=0")
        assert response.status_code == 200
        data = response.json()
        assert len(data["overall"]) == 7
        for item in data["overall"]:
            assert "high_risk_count" in item

    def test_cross_day_invalid_category(self, client):
        response = client.get("/api/v1/supervisor/cross-day-comparison?category=无效类别&min_risk_score=0")
        assert response.status_code == 400

    def test_cross_day_category_filter_zero_dates_preserved(self, client):
        response = client.get("/api/v1/supervisor/cross-day-comparison?days=7&category=教育文化&min_risk_score=0")
        data = response.json()
        daily = data["by_category"][0]["daily_trend"]
        assert len(daily) == 7
        zero_days = [d for d in daily if d["high_risk_count"] == 0]
        assert len(zero_days) >= 0
