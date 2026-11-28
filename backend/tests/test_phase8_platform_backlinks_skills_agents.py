"""Phase 8 Tests — §8A/G Deliverables & Images, §8B Multi-Site Backlink Library, §8C Messaging Gateway, §8D/L.1 Skills, §8F/L.4 Agents & I2I."""

from __future__ import annotations

import httpx
from openpyxl import load_workbook
import pytest
from sqlalchemy.orm import Session

from app.models.instinct import I2IBundle
from app.services.accounts_sheet import get_accounts_spreadsheet_path
from app.skills.coded.catalog import CODED_SKILLS_SPECS


def test_web_app_presentation_and_honest_image_artifacts(owner_session: dict):
    client = owner_session["client"]
    headers = owner_session["headers"]

    # 1. Build interactive web app + preview & download
    app_res = client.post("/api/artifacts/build-app", json={"prompt": "متجر إلكتروني لمنتجات رقمية"}, headers=headers)
    assert app_res.status_code == 200
    app_art = app_res.json()["artifact"]
    assert app_art["kind"] in {"web_app", "html_app"}

    prev = client.get(app_art["preview_url"], headers=headers)
    assert prev.status_code == 200 and "<html" in prev.text.lower()
    dl = client.get(app_art["download_url"], headers=headers)
    assert dl.status_code == 200

    # 2. Build presentation deck
    deck_res = client.post(
        "/api/artifacts/presentations",
        json={"title": "خطة المنصة", "topic": "وكلاء ذكاء اصطناعي ومتصفح حي"},
        headers=headers,
    )
    assert deck_res.status_code == 200
    deck_art = deck_res.json()["artifact"]
    assert deck_art["kind"] == "presentation"
    assert client.get(deck_art["preview_url"], headers=headers).status_code == 200

    # 3. Image generation stops honestly when no image provider key exists (real mode)
    real_img = client.post(
        "/api/images/generate",
        json={"prompt": "شعار تقني حديث", "mock_mode": False},
        headers=headers,
    )
    assert real_img.status_code == 200
    assert real_img.json()["status"] == "missing_image_provider"
    assert real_img.json()["is_mock"] is False

    # 4. Explicit mock mode generates valid PNG with honest mock flag
    mock_img = client.post(
        "/api/images/generate",
        json={"prompt": "شعار تقني حديث", "mock_mode": True},
        headers=headers,
    )
    assert mock_img.status_code == 200
    img_data = mock_img.json()
    assert img_data["status"] == "simulated" and img_data["is_mock"] is True
    img_bytes = client.get(img_data["artifact"]["preview_url"], headers=headers).content
    assert img_bytes[:8] == b"\x89PNG\r\n\x1a\n"


def test_multi_site_backlink_library_and_placements_sheet(owner_session: dict, monkeypatch: pytest.MonkeyPatch):
    client = owner_session["client"]
    headers = owner_session["headers"]

    # Seeded library has >= 2 sites with SITE.md on disk
    lib_res = client.get("/api/backlinks/library", headers=headers)
    assert lib_res.status_code == 200
    sites = lib_res.json()["sites"]
    assert len(sites) >= 2
    assert all("# Playbook:" in s["playbook_md"] for s in sites[:2])

    # Discover a new site (mocked HTTP probe)
    class FakeResp:
        status_code = 200
        text = '<html><form><input name="email"/><input name="website"/><textarea name="bio"></textarea></form></html>'

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url: str):
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    disc = client.post(
        "/api/backlinks/library/discover",
        json={"domain_or_url": "https://indiehackers.com", "category": "profile"},
        headers=headers,
    )
    assert disc.status_code == 200
    assert disc.json()["site"]["domain"] == "indiehackers.com"

    # 1 Target Site Project × 2 Library Sites × 2 Distinct Emails
    proj = client.post(
        "/api/backlinks/targets/projects",
        json={"name": "متجر النخبة", "url": "https://nukhba-shop.example.com"},
        headers=headers,
    ).json()["project"]

    p1 = client.post(
        "/api/backlinks/placements",
        json={
            "target_project_id": proj["id"],
            "library_site_id": sites[0]["id"],
            "email_used": "seo.alpha@example.com",
            "profile_url": f"https://{sites[0]['domain']}/nukhba",
            "live": True,
            "status": "live",
        },
        headers=headers,
    )
    p2 = client.post(
        "/api/backlinks/placements",
        json={
            "target_project_id": proj["id"],
            "library_site_id": sites[1]["id"],
            "email_used": "seo.beta@example.com",
            "profile_url": f"https://{sites[1]['domain']}/nukhba",
            "live": True,
            "status": "live",
        },
        headers=headers,
    )
    assert p1.status_code == 200 and p2.status_code == 200

    listed = client.get(f"/api/backlinks/placements?target_project_id={proj['id']}", headers=headers).json()["placements"]
    assert len(listed) == 2
    assert {row["email_used"] for row in listed} == {"seo.alpha@example.com", "seo.beta@example.com"}

    # Verify synced into isolated tmp_path accounts.xlsx
    xlsx_path = get_accounts_spreadsheet_path()
    assert xlsx_path.exists()
    wb = load_workbook(xlsx_path)
    ws = wb.active
    emails_in_sheet = {str(ws.cell(row=r, column=2).value) for r in range(2, ws.max_row + 1)}
    assert {"seo.alpha@example.com", "seo.beta@example.com"}.issubset(emails_in_sheet)


def test_messaging_gateway_cross_platform_shared_thread_and_voice(owner_session: dict):
    client = owner_session["client"]
    headers = owner_session["headers"]

    for ch in ("whatsapp", "telegram"):
        cfg = client.put(
            f"/api/messaging/channels/{ch}",
            json={"enabled": True, "config": {"bot_username": f"bot_{ch}"}, "continuity_mode": "shared_owner_thread"},
            headers=headers,
        )
        assert cfg.status_code == 200

    wa = client.post(
        "/api/messaging/webhook/whatsapp",
        json={"peer_id": "owner", "text": "مرحباً من واتساب", "mock_mode": True},
        headers=headers,
    )
    tg = client.post(
        "/api/messaging/webhook/telegram",
        json={"peer_id": "owner", "text": "تابع نفس المحادثة من تيليجرام", "mock_mode": True},
        headers=headers,
    )
    assert wa.status_code == 200 and tg.status_code == 200
    assert wa.json()["thread_id"] == tg.json()["thread_id"]

    logs = client.get("/api/messaging/logs", headers=headers).json()["logs"]
    channels_logged = {l["channel"] for l in logs}
    assert {"whatsapp", "telegram"}.issubset(channels_logged)

    vt = client.post("/api/voice/transcribe", json={"transcript_hint": "افحص تقارير السيو غداً"}, headers=headers)
    assert vt.status_code == 200 and "افحص تقارير السيو" in vt.json()["transcript"]


def test_prompt_registered_skill_fires_next_turn_and_coded_skills_work(owner_session: dict):
    client = owner_session["client"]
    headers = owner_session["headers"]

    reg = client.post(
        "/api/skills/register-from-prompt",
        json={"prompt": "invoice-audit: مراجعة الفواتير واستخراج الضريبة والمجموع النهائي"},
        headers=headers,
    )
    assert reg.status_code == 200
    sk = reg.json()["skill"]
    assert sk["slug"] == "invoice-audit"

    # Verify next chat turn runs newly registered skill without restarting app
    th = client.post("/api/chat/threads", json={"title": "تجربة مهارة جديدة"}, headers=headers).json()["thread"]
    turn = client.post(
        f"/api/chat/threads/{th['id']}/messages",
        json={"content": "شغّل مراجعة الفواتير والضريبة لشهر مارس", "mock_mode": True},
        headers=headers,
    )
    assert turn.status_code == 200
    assert turn.json()["intent"] == "skill:invoice-audit"

    imp = client.post(f"/api/skills/{sk['slug']}/improve", json={"note": "أضف تحققاً من العملة"}, headers=headers)
    assert imp.status_code == 200

    # Coded skills catalog + schema validation + execution
    coded_res = client.get("/api/skills/coded", headers=headers)
    assert coded_res.status_code == 200
    assert coded_res.json()["coded_count"] >= 13
    assert len(CODED_SKILLS_SPECS) >= 13

    bad_flights = client.post("/api/skills/coded/flights/execute", json={"params": {}}, headers=headers)
    assert bad_flights.status_code == 400

    ok_flights = client.post(
        "/api/skills/coded/flights/execute",
        json={"params": {"origin": "CAI", "destination": "RUH"}},
        headers=headers,
    )
    assert ok_flights.status_code == 200 and ok_flights.json()["status"] == "awaiting_approval"

    ok_files = client.post(
        "/api/skills/coded/files/execute",
        json={"params": {"action": "write", "filename": "phase8_note.txt", "content": "hello platform"}},
        headers=headers,
    )
    assert ok_files.status_code == 200 and ok_files.json()["bytes"] > 0


def test_custom_agents_crud_and_signed_i2i_delegation(owner_session: dict, temp_db_session: Session):
    client = owner_session["client"]
    headers = owner_session["headers"]

    ag_a = client.post(
        "/api/agents/custom",
        json={"name": "Lead Strategist", "description": "Coordinates tasks", "skills": ["deep-research"]},
        headers=headers,
    ).json()["agent"]
    ag_b = client.post(
        "/api/agents/custom",
        json={"name": "SQLite Reviewer", "description": "Audits data", "skills": ["files"]},
        headers=headers,
    ).json()["agent"]

    paused = client.patch(f"/api/agents/custom/{ag_b['id']}", json={"status": "paused"}, headers=headers)
    assert paused.status_code == 200 and paused.json()["agent"]["status"] == "paused"
    resumed = client.patch(f"/api/agents/custom/{ag_b['id']}", json={"status": "idle"}, headers=headers)
    assert resumed.status_code == 200 and resumed.json()["agent"]["status"] == "idle"

    deleg = client.post(
        "/api/agents/delegate",
        json={
            "parent_agent_id": ag_a["id"],
            "child_agent_id": ag_b["id"],
            "prompt": "راجع سجل الأداء ولخصه",
            "depth": 1,
        },
        headers=headers,
    )
    assert deleg.status_code == 200
    d_row = deleg.json()["delegation"]
    assert d_row["trust_bundle_id"]
    bundle = temp_db_session.get(I2IBundle, d_row["trust_bundle_id"])
    assert bundle is not None and len((bundle.permissions_json or {}).get("signature", "")) == 64

    # Enforce MAX_DELEGATION_DEPTH = 2
    too_deep = client.post(
        "/api/agents/delegate",
        json={"parent_agent_id": ag_a["id"], "child_agent_id": ag_b["id"], "prompt": "deep call", "depth": 3},
        headers=headers,
    )
    assert too_deep.status_code == 400

    found = client.get("/api/agents/delegations?q=سجل", headers=headers).json()["delegations"]
    assert any(x["id"] == d_row["id"] for x in found)

    canceled = client.post(f"/api/agents/delegations/{d_row['id']}/cancel", headers=headers)
    assert canceled.status_code == 200 and canceled.json()["delegation"]["status"] == "canceled"
