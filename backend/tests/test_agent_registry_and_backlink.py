"""Tests for Agent Registry, Dispatcher, Backlink Agent mock E2E run, Importer, and password-free Export."""

from __future__ import annotations

import time

from sqlalchemy.orm import Session

from app.models.backlinks import BlAccount, BlLink, BlSite, BlStep


def test_registry_dispatcher_backlink_e2e_and_importer(owner_session, temp_db_session: Session):
    client = owner_session["client"]
    headers = owner_session["headers"]

    # 1. Registry discovery + 20-agent catalog + 38 Instinct skills
    disc = client.post("/api/agents/discover", headers=headers)
    assert disc.status_code == 200
    assert "backlink" in disc.json()["keys"]
    assert "link_monitor" in disc.json()["keys"]

    agents_res = client.get("/api/agents", headers=headers).json()
    assert len(agents_res["agents"]) >= 2
    assert len(agents_res["catalog"]) >= 18

    skills_res = client.get("/api/skills", headers=headers).json()
    assert skills_res["platform_skills_count"] == 38
    assert skills_res["site_playbooks_count"] == 18

    # 2. Dispatcher matches backlink requests and rejects unregistered tasks
    match_ok = client.post("/api/tasks/dispatch", headers=headers, json={"request_text": "سجل باك لينك لموقع moaazhassan.com"})
    assert match_ok.status_code == 200
    assert match_ok.json()["matched"] is True
    assert match_ok.json()["agent_key"] == "backlink"

    match_no = client.post("/api/tasks/dispatch", headers=headers, json={"request_text": "احجز تذكرة طيران إلى دبي"})
    assert match_no.status_code == 200
    assert match_no.json()["matched"] is False

    # 3. Preview & Bulk Approval Gate (> 20 items)
    prev_bulk = client.post(
        "/api/tasks/preview",
        headers=headers,
        json={"agent_id": "backlink", "input_json": {"target_domain": "moaazhassan.com", "max_per_day": 25, "mock_mode": True}},
    )
    assert prev_bulk.status_code == 200
    assert prev_bulk.json()["requires_approval"] is True
    assert "bulk_batch_over_20" in prev_bulk.json()["approval_reasons"]

    # 4. Run Backlink Agent in mock mode on the 10 default sites
    task_res = client.post(
        "/api/tasks",
        headers=headers,
        json={
            "agent_id": "backlink",
            "request_text": "Run 10 mock backlinks",
            "input_json": {"target_domain": "moaazhassan.com", "max_per_day": 10, "mock_mode": True},
        },
    )
    assert task_res.status_code == 200
    run_id = task_res.json()["run"]["id"]

    # Wait briefly for in-process async task completion
    run_data = None
    for _ in range(30):
        r = client.get(f"/api/runs/{run_id}", headers=headers).json()
        if r["run"]["status"] in {"done", "failed", "canceled"}:
            run_data = r
            break
        time.sleep(0.1)

    assert run_data is not None
    assert run_data["run"]["status"] == "done"
    summary = run_data["run"]["summary_json"]
    assert summary["processed"] == 10
    assert summary["excluded_noindex"] >= 1  # linktr.ee skipped automatically
    assert summary["live"] >= 6

    # Verify bl_steps recorded with value_key and NO raw passwords
    accounts = temp_db_session.query(BlAccount).all()
    assert len(accounts) >= 6
    steps = temp_db_session.query(BlStep).all()
    assert len(steps) >= 30
    links = temp_db_session.query(BlLink).all()
    assert any(l.link_type == "dofollow" for l in links)
    assert any(l.link_type == "nofollow" for l in links)

    # Reveal password works and is audited; export NEVER includes password_enc
    reveal = client.post(f"/api/backlinks/accounts/{accounts[0].id}/reveal", headers=headers, json={})
    assert reveal.status_code == 200
    assert len(reveal.json()["password"]) >= 12

    csv_export = client.get("/api/backlinks/export?table=accounts", headers=headers)
    assert csv_export.status_code == 200
    assert reveal.json()["password"] not in csv_export.text
    assert "password_enc" not in csv_export.text

    # 5. CSV Import preview + commit + JSON steps import
    csv_bytes = "domain,signup_url,category\nnewsite1.example,https://newsite1.example/join,profile\ngravatar.com,https://gravatar.com/signup,profile\n".encode("utf-8")
    imp_prev = client.post(
        "/api/backlinks/import/preview",
        headers=headers,
        files={"file": ("farm.csv", csv_bytes, "text/csv")},
    )
    assert imp_prev.status_code == 200
    assert imp_prev.json()["total_rows"] == 2

    imp_commit = client.post(
        "/api/backlinks/import/commit",
        headers=headers,
        files={"file": ("farm.csv", csv_bytes, "text/csv")},
        data={"mapping_json": '{"domain": "domain", "signup_url": "signup_url", "category": "category"}'},
    )
    assert imp_commit.status_code == 200
    assert imp_commit.json()["imported"] == 1
    assert imp_commit.json()["duplicate"] == 1
