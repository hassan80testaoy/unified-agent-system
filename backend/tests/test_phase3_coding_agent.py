"""Phase 3 Coding Agent test suite (§1A-§1G)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from unittest.mock import MagicMock, patch

from app.models.coding import CodingThread
from app.services.coding_agent import resume_interrupted_threads


def _auth_headers(owner_session: dict) -> dict[str, str]:
    tok = owner_session["data"]["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def test_plan_before_code_and_real_browser_app_build_with_self_fix(client, owner_session, tmp_path):
    ws_dir = tmp_path / "todo_app"
    headers = _auth_headers(owner_session)

    # 1. Create thread
    create_res = client.post(
        "/api/threads",
        json={"title": "مدير مهام عربي", "workspace_dir": str(ws_dir)},
        headers=headers,
    )
    assert create_res.status_code == 200, create_res.text
    tid = create_res.json()["thread"]["id"]

    # 2. Initial prompt stops at Plan Review BEFORE touching any code files
    plan_res = client.post(
        f"/api/threads/{tid}/messages",
        json={"content": "ابني تطبيق قائمة مهام بالعربية"},
        headers=headers,
    )
    assert plan_res.status_code == 200, plan_res.text
    body = plan_res.json()
    assert body["awaiting_plan_approval"] is True
    assert body["thread"]["status"] == "awaiting_plan_approval"
    assert body["thread"]["plan_status"] == "pending_review"
    assert len(body["thread"]["design_options"]) == 3
    assert {a["kind"] for a in body["artifacts"]} >= {"plan", "task_list", "design"}
    assert not (ws_dir / "index.html").exists()
    assert not (ws_dir / "app.js").exists()

    # 3. Approve plan + design -> real browser catches initial JS error, auto-fixes, verifies 0 console errors + real PNG
    app_res = client.post(
        f"/api/threads/{tid}/approve-plan",
        json={"design_index": 1, "inject_initial_bug": True, "skip_browser_verify": False},
        headers=headers,
    )
    assert app_res.status_code == 200, app_res.text
    app_body = app_res.json()
    assert app_body["thread"]["status"] == "done", app_body
    assert app_body["browser_verify"]["ok"] is True
    assert app_body["browser_verify"]["console_errors"] == []

    # Real error-capture -> auto-fix message occurred before final done
    msg_types = [m["type"] for m in app_body["thread"]["messages"]]
    assert "browser_error_fix" in msg_types
    fix_msgs = [m for m in app_body["thread"]["messages"] if m["type"] == "browser_error_fix"]
    assert any("renderTodoItems" in str(m["content"]) or "ReferenceError" in str(m["content"]) for m in fix_msgs)

    # Real PNG screenshot & recording bundle stored on disk & downloadable via API
    arts_res = client.get(f"/api/threads/{tid}/artifacts", headers=headers)
    assert arts_res.status_code == 200
    arts = arts_res.json()["artifacts"]
    shots = [a for a in arts if a["kind"] == "screenshot"]
    recs = [a for a in arts if a["kind"] == "recording"]
    assert shots and recs

    png_dl = client.get(f"/api/threads/{tid}/artifacts/{shots[0]['id']}/file", headers=headers)
    assert png_dl.status_code == 200
    assert png_dl.content.startswith(b"\x89PNG")

    rec_dl = client.get(f"/api/threads/{tid}/artifacts/{recs[0]['id']}/file", headers=headers)
    assert rec_dl.status_code == 200
    rec_json = rec_dl.json()
    assert rec_json["console_errors"] == []
    assert len(rec_json["steps"]) >= 2


def test_just_do_it_bypasses_plan_gate(client, owner_session, tmp_path):
    headers = _auth_headers(owner_session)
    ws_dir = tmp_path / "bypass_ws"
    tid = client.post(
        "/api/threads",
        json={"title": "Bypass test", "workspace_dir": str(ws_dir)},
        headers=headers,
    ).json()["thread"]["id"]

    res = client.post(
        f"/api/threads/{tid}/messages",
        json={"content": "just do it: write hello.txt: salam-direct", "skip_browser_verify": True},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    assert res.json()["thread"]["status"] == "done"
    assert (ws_dir / "hello.txt").read_text(encoding="utf-8") == "salam-direct"


def test_undo_rewinds_files_on_disk_and_fork(client, owner_session, tmp_path):
    headers = _auth_headers(owner_session)
    ws_dir = tmp_path / "undo_ws"
    tid = client.post(
        "/api/threads",
        json={"title": "Undo & Fork", "workspace_dir": str(ws_dir)},
        headers=headers,
    ).json()["thread"]["id"]

    # Turn 1 writes v1 directly
    r1 = client.post(
        f"/api/threads/{tid}/messages",
        json={"content": "نفذ مباشرة: write notes.txt: version-one", "skip_browser_verify": True},
        headers=headers,
    ).json()
    seq_after_v1 = r1["thread"]["messages"][-1]["seq"]
    assert (ws_dir / "notes.txt").read_text(encoding="utf-8") == "version-one"

    # Turn 2 overwrites with v2 and creates a new extra.txt
    client.post(
        f"/api/threads/{tid}/messages",
        json={"content": "write notes.txt: version-two", "skip_browser_verify": True},
        headers=headers,
    )
    client.post(
        f"/api/threads/{tid}/messages",
        json={"content": "write extra.txt: disposable", "skip_browser_verify": True},
        headers=headers,
    )
    assert (ws_dir / "notes.txt").read_text(encoding="utf-8") == "version-two"
    assert (ws_dir / "extra.txt").exists()

    # Undo back to seq_after_v1 -> BOTH messages AND disk files rewind!
    undo_res = client.post(
        f"/api/threads/{tid}/undo",
        json={"to_seq": seq_after_v1},
        headers=headers,
    )
    assert undo_res.status_code == 200, undo_res.text
    assert (ws_dir / "notes.txt").read_text(encoding="utf-8") == "version-one"
    assert not (ws_dir / "extra.txt").exists()
    assert len(undo_res.json()["thread"]["messages"]) == seq_after_v1

    # Fork from seq_after_v1 into isolated git worktree
    fork_res = client.post(
        f"/api/threads/{tid}/fork",
        json={"from_seq": seq_after_v1, "new_title": "Forked branch", "use_worktree": True},
        headers=headers,
    )
    assert fork_res.status_code == 200, fork_res.text
    forked = fork_res.json()["thread"]
    assert forked["parent_thread_id"] == tid
    assert forked["worktree_branch"]
    assert Path(forked["worktree_path"]).exists()


def test_model_race_merges_winner_keeps_losers(client, owner_session, tmp_path):
    headers = _auth_headers(owner_session)
    ws_dir = tmp_path / "race_ws"
    tid = client.post(
        "/api/threads",
        json={"title": "Race task", "workspace_dir": str(ws_dir)},
        headers=headers,
    ).json()["thread"]["id"]

    race_res = client.post(
        f"/api/threads/{tid}/race",
        json={
            "prompt": "Implement add(a, b)",
            "test_command": 'python -c "import solution; assert solution.solve(2, 3) == 5"',
            "candidates": [
                {
                    "provider_id": "loser-prov",
                    "model_name": "buggy-model",
                    "files": {"solution.py": "def solve(a, b):\n    return a - b\n"},
                },
                {
                    "provider_id": "winner-prov",
                    "model_name": "exact-model",
                    "files": {"solution.py": "def solve(a, b):\n    return a + b\n"},
                },
            ],
        },
        headers=headers,
    )
    assert race_res.status_code == 200, race_res.text
    race = race_res.json()["race"]
    assert race["winner_provider"] == "winner-prov"
    assert race["merged"] is True
    assert "return a + b" in (ws_dir / "solution.py").read_text(encoding="utf-8")

    # Loser worktree MUST still exist for inspection
    loser = next(c for c in race["candidates"] if c["provider_id"] == "loser-prov")
    assert loser["passed"] is False
    assert Path(loser["worktree_dir"]).exists()


def test_todo_mid_run_edit_goals_and_resume_after_restart(client, owner_session, temp_db_session, tmp_path):
    headers = _auth_headers(owner_session)
    ws_dir = tmp_path / "todo_goal_ws"
    tid = client.post(
        "/api/threads",
        json={"title": "Goals & Resume", "workspace_dir": str(ws_dir)},
        headers=headers,
    ).json()["thread"]["id"]

    # Edit todo mid-run and confirm it feeds into next turn context_snapshot
    todo_res = client.put(
        f"/api/threads/{tid}/todo",
        json={"items": [{"id": "t-custom", "text": "خطوة مخصصة من المستخدم", "status": "pending"}]},
        headers=headers,
    )
    assert todo_res.status_code == 200
    assert any(x["text"] == "خطوة مخصصة من المستخدم" for x in todo_res.json()["context_snapshot"]["todo"])

    # `/goal` creates autonomous goal
    goal_res = client.post(
        f"/api/threads/{tid}/messages",
        json={"content": "/goal write changelog.md: initial", "skip_browser_verify": True},
        headers=headers,
    )
    assert goal_res.status_code == 200, goal_res.text
    goal_id = goal_res.json()["goal"]["id"]
    step_res = client.post(f"/api/goals/{goal_id}/step", headers=headers)
    assert step_res.status_code == 200
    assert step_res.json()["goal"]["checkpoint"]["completed_steps"] >= 1

    # Simulate interrupted thread before restart -> auto-resume recovers cleanly
    client.patch(f"/api/threads/{tid}", json={"status": "interrupted"}, headers=headers)
    row = temp_db_session.get(CodingThread, tid)
    assert row is not None and row.status == "interrupted"
    resumed = resume_interrupted_threads(temp_db_session)
    temp_db_session.commit()
    assert any(r["thread_id"] == tid for r in resumed)
    after = client.get(f"/api/threads/{tid}", headers=headers).json()["thread"]
    assert any(m["type"] == "auto_resume" for m in after["messages"])
    assert after["checkpoint"]["resumed_count"] >= 1


def test_subagents_hooks_worktree_parallel_cap_and_sdks(client, owner_session, tmp_path):
    headers = _auth_headers(owner_session)
    ws_dir = tmp_path / "hooks_wt_ws"
    tid = client.post(
        "/api/threads",
        json={"title": "Hooks & Worktree", "workspace_dir": str(ws_dir)},
        headers=headers,
    ).json()["thread"]["id"]

    # 1. Blocking pre_tool hook prevents edit
    bad_cmd = 'python -c "import sys; sys.exit(1)"'
    hook_res = client.post(
        "/api/hooks",
        json={"name": "deny-write", "event": "pre_tool", "command": bad_cmd, "block_on_fail": True},
        headers=headers,
    )
    assert hook_res.status_code == 200
    hook_id = hook_res.json()["hook"]["id"]

    blocked = client.post(
        f"/api/threads/{tid}/messages",
        json={"content": "just do it: write blocked.txt: nope", "skip_browser_verify": True},
        headers=headers,
    )
    assert blocked.status_code == 200
    assert blocked.json()["thread"]["status"] == "blocked"
    assert not (ws_dir / "blocked.txt").exists()

    # Remove blocking hook, add worktree, write file & merge back
    client.delete(f"/api/hooks/{hook_id}", headers=headers)
    wt = client.post(f"/api/threads/{tid}/worktree", json={"branch_name": "feat/wt-test"}, headers=headers)
    assert wt.status_code == 200

    ok_res = client.post(
        f"/api/threads/{tid}/messages",
        json={"content": "just do it: write feature.txt: wt-ok", "skip_browser_verify": True},
        headers=headers,
    )
    assert ok_res.status_code == 200
    assert ok_res.json()["thread"]["status"] == "done"

    merge_res = client.post(f"/api/threads/{tid}/worktree/merge", headers=headers)
    assert merge_res.status_code == 200
    assert merge_res.json()["merged"] is True
    assert (ws_dir / "feature.txt").read_text(encoding="utf-8") == "wt-ok"

    # 2. Subagent launch
    sub_res = client.post(
        f"/api/threads/{tid}/subagents",
        json={"role": "reviewer", "prompt": "Check feature.txt", "use_worktree": True},
        headers=headers,
    )
    assert sub_res.status_code == 200
    assert sub_res.json()["subagent"]["subagent_role"] == "reviewer"

    # 3. Parallel thread cap enforcement (default 5)
    for i in range(5):
        st = client.post(
            "/api/threads",
            json={"title": f"Parallel-{i}", "workspace_dir": str(tmp_path / f"p{i}")},
            headers=headers,
        ).json()["thread"]["id"]
        client.patch(f"/api/threads/{st}", json={"status": "running"}, headers=headers)

    cap_hit = client.post(
        f"/api/threads/{tid}/messages",
        json={"content": "just do it: write overcap.txt: 1", "skip_browser_verify": True},
        headers=headers,
    )
    assert cap_hit.status_code == 429

    # 4. Python stdlib SDK test
    sdk_dir = Path(__file__).resolve().parents[2] / "sdk" / "python"
    sys.path.insert(0, str(sdk_dir))
    import uas_sdk  # noqa: PLC0415

    fake_resp = MagicMock()
    fake_resp.read.return_value = json.dumps({"status": "ok"}).encode("utf-8")
    fake_resp.__enter__.return_value = fake_resp
    with patch.object(uas_sdk, "urlopen", return_value=fake_resp) as mock_open:
        client_sdk = uas_sdk.UnifiedAgentSDK(base_url="http://127.0.0.1:8010", token="tok-123")
        out = client_sdk.health()
        assert out == {"status": "ok"}
        req_sent = mock_open.call_args[0][0]
        assert req_sent.full_url == "http://127.0.0.1:8010/api/health"
        assert req_sent.headers["Authorization"] == "Bearer tok-123"
