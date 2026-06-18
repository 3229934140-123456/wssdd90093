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
        data = response.json()
        assert "message" in data
        assert "docs" in data

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
        assert "analyzed_at" in result

        risk = result["risk_assessment"]
        assert risk["risk_level"] in ["低", "中", "高", "极高"]
        assert 0 <= risk["risk_score"] <= 100
        assert risk["category"] in ["公共安全", "民生政策", "医疗健康", "财经金融", "教育文化", "其他"]

    def test_analyze_rumor_with_url(self, client):
        payload = {
            "content_url": "https://weibo.com/123456/status/999999",
            "ticket_id": "TICKET-002",
            "submitter": "auditor_02"
        }

        response = client.post("/api/v1/audit/analyze", json=payload)
        assert response.status_code == 200

        data = response.json()
        assert data["ticket_id"] == "TICKET-002"
        assert data["result"]["earliest_source"]["platform"] == "微博"

    def test_analyze_rumor_duplicate(self, client):
        payload = {
            "text_content": "重复查询测试内容",
            "ticket_id": "TICKET-003",
            "submitter": "auditor_01"
        }

        response1 = client.post("/api/v1/audit/analyze", json=payload)
        assert response1.status_code == 200
        query_id1 = response1.json()["query_id"]

        payload2 = {
            "text_content": "重复查询测试内容",
            "ticket_id": "TICKET-003",
            "submitter": "auditor_02"
        }
        response2 = client.post("/api/v1/audit/analyze", json=payload2)
        assert response2.status_code == 200
        assert response2.json()["query_id"] == query_id1

    def test_analyze_rumor_missing_fields(self, client):
        payload = {
            "ticket_id": "TICKET-004",
            "submitter": "auditor_01"
        }

        response = client.post("/api/v1/audit/analyze", json=payload)
        assert response.status_code == 422
        data = response.json()
        assert "必须提供文本内容、话题标签或内容链接中的至少一项" in str(data["detail"])

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
        data = response.json()
        assert len(data) >= 3

    def test_list_queries_with_filters(self, client):
        for i in range(2):
            payload = {
                "text_content": f"测试内容{i}",
                "ticket_id": f"TICKET-FILTER-{i}",
                "submitter": "auditor_01"
            }
            client.post("/api/v1/audit/analyze", json=payload)

        response = client.get("/api/v1/audit/queries?submitter=auditor_01")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 2

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
        data = response.json()
        assert data["query_id"] == query_id
        assert data["ticket_id"] == "TICKET-GET-001"

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
        data = response.json()
        assert len(data) >= 5

    def test_list_rumors_with_category_filter(self, client):
        for i in range(3):
            payload = {
                "text_content": f"医疗健康测试{i} 疫苗病毒致癌",
                "ticket_id": f"TICKET-CAT-{i}",
                "submitter": "auditor_01"
            }
            client.post("/api/v1/audit/analyze", json=payload)

        response = client.get("/api/v1/supervisor/rumors?category=医疗健康&limit=10")
        assert response.status_code == 200
        data = response.json()
        for item in data:
            assert item["category"] == "医疗健康"

    def test_list_rumors_sorted(self, client):
        for i in range(5):
            payload = {
                "text_content": f"排序测试{i}",
                "ticket_id": f"TICKET-SORT-{i}",
                "submitter": "auditor_01"
            }
            client.post("/api/v1/audit/analyze", json=payload)

        response = client.get("/api/v1/supervisor/rumors?sort_by=risk_score&sort_order=desc&limit=10")
        assert response.status_code == 200
        data = response.json()

        scores = [item["risk_score"] for item in data]
        assert scores == sorted(scores, reverse=True)

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

    def test_daily_high_risk_with_invalid_date(self, client):
        response = client.get("/api/v1/supervisor/daily-high-risk?target_date=invalid-date")
        assert response.status_code == 400

    def test_daily_high_risk_with_invalid_category(self, client):
        response = client.get("/api/v1/supervisor/daily-high-risk?category=无效类别")
        assert response.status_code == 400

    def test_spread_track(self, client):
        payload = {
            "text_content": "传播追踪测试 疫情病毒",
            "ticket_id": "TICKET-TRACK-001",
            "submitter": "auditor_01"
        }
        create_response = client.post("/api/v1/audit/analyze", json=payload)

        rumors_response = client.get("/api/v1/supervisor/rumors?limit=1")
        rumor_id = rumors_response.json()[0]["rumor_id"]

        response = client.get(f"/api/v1/supervisor/rumors/{rumor_id}/spread-track")
        assert response.status_code == 200
        data = response.json()
        assert data["rumor_id"] == rumor_id
        assert "trend" in data

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
        assert "intervention_time" in data

        rumors_after = client.get(f"/api/v1/supervisor/rumors?handle_status=已处置&limit=1")
        assert len(rumors_after.json()) >= 1

    def test_record_intervention_not_found(self, client):
        response = client.post("/api/v1/supervisor/rumors/99999/intervene")
        assert response.status_code == 404

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
        data = response.json()
        assert data["rumor_id"] == rumor_id
        assert "total_accounts" in data
        assert len(data["accounts"]) >= 3

    def test_daily_high_risk_grouped(self, client):
        categories = ["医疗健康", "公共安全", "民生政策", "财经金融"]
        for i, cat in enumerate(categories):
            for j in range(i + 1):
                keyword = {
                    "医疗健康": "病毒疫情",
                    "公共安全": "火灾事故",
                    "民生政策": "退休补贴",
                    "财经金融": "股票暴跌"
                }[cat]
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
        assert "total_high_risk" in data
        assert "by_category" in data

        for group in data["groups"]:
            assert "category" in group
            assert "count" in group
            assert "rumors" in group
            assert group["count"] == len(group["rumors"])
            for rumor in group["rumors"]:
                assert rumor["category"] == group["category"]

    def test_daily_high_risk_grouped_sorted(self, client):
        for i in range(5):
            payload = {
                "text_content": f"分组排序测试{i} 病毒疫情",
                "ticket_id": f"TICKET-GRPSORT-{i}",
                "submitter": "auditor_01"
            }
            client.post("/api/v1/audit/analyze", json=payload)

        response = client.get("/api/v1/supervisor/daily-high-risk/grouped?item_sort_by=total_shares&sort_order=desc&min_risk_score=0")
        assert response.status_code == 200
        data = response.json()
        assert data["sort_by"] == "total_shares"

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
        assert "intervention_stats" in data

        client.post(f"/api/v1/supervisor/rumors/{rumor_id}/intervene")

        response2 = client.get(f"/api/v1/supervisor/rumors/{rumor_id}/spread-track")
        assert response2.status_code == 200
        data2 = response2.json()

        assert data2["intervention_time"] is not None
        assert "intervention_stats" in data2
        assert "observation_status" in data2
        assert data2["observation_status"] in ["待观察", "处置后暂无数据"]

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
        assert "total_high_risk" in data
        assert "export_time" in data
        assert "groups" in data
        assert "by_category_count" in data

        for group in data["groups"]:
            assert "category" in group
            assert "rumors" in group
            for rumor in group["rumors"]:
                assert "risk_level" in rumor
                assert "risk_score" in rumor
                assert "total_shares" in rumor
                assert "main_channels" in rumor
                assert "handle_status" in rumor
                assert "debunk_status" in rumor

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
        assert "attachment" in response.headers.get("content-disposition", "")

        content = response.text
        assert "序号" in content
        assert "类别" in content
        assert "标题" in content
        assert "风险等级" in content
        assert "总传播量" in content
        assert "处置状态" in content

    def test_tips_correspond_with_data(self, client):
        payload = {
            "text_content": "提示一致性测试",
            "topic_tags": ["疫情", "病毒"],
            "ticket_id": "TICKET-TIP-CONSIST-001",
            "submitter": "auditor_01"
        }

        response = client.post("/api/v1/audit/analyze", json=payload)
        assert response.status_code == 200
        data = response.json()
        result = data["result"]

        channels = result["main_channels"]
        tips = result["actionable_tips"]
        debunk_info = result["debunk_info"]

        regional_tips = [t for t in tips if t["tip_type"] == "regional_spike"]
        rapid_with_region = [c for c in channels if c["is_rapid_growth"] and c["region"]]

        if rapid_with_region:
            assert len(regional_tips) >= 1
            rc = rapid_with_region[0]
            assert rc["region"] in regional_tips[0]["content"]

        debunk_tips = [t for t in tips if t["tip_type"] == "debunk_coverage"]
        if debunk_info["exists"]:
            assert len(debunk_tips) >= 1
            assert debunk_info["debunk_authority"] in debunk_tips[0]["content"]
        else:
            assert len(debunk_tips) == 0
