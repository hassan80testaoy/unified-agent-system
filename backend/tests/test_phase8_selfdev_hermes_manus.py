"""Phase 8 Tests — §8E Self-Dev Worktree & §0 Guard, §8H Hermes/Aider Patterns, §8I Manus Projects/Wide Research, §8J Grok xAI & Eval Harness."""

from __future__ import annotations

from pathlib import Path
import subprocess

from sqlalchemy.orm import Session
import yaml

from app.models.phase5 import AutomationTrigger
from app.models.phase8 import MessagingEventLog
from app.services.at_mentions import expand_at_mentions
from app.services.dev_tools_runner import opt_in_auto_git_commit, run_workspace_checks
from app.services.self_dev_service import decide_self_dev_proposal, propose_self_dev_worktree
from app.services.triggers_service import fire_trigger


def _init_temp_git_repo(repo_dir: Path) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(repo_dir), check=True, capture_output=True)
    (repo_dir / "app_mod.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (repo_dir / "tests").mkdir(exist_ok=True)
    (repo_dir / "tests" / "test_sample.py").write_text(
        "from app_mod import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=str(repo_dir), check=True, capture_output=True)


def test_self_dev_isolated_worktree_diff_pytest_approval_and_section0_guard(
    owner_session: dict,
    temp_db_session: Session,
    tmp_path: Path,
):
    ws_id = owner_session["data"]["workspace"]["id"]
    uid = owner_session["data"]["user"]["id"]
    tmp_repo = tmp_path / "isolated_repo"
    _init_temp_git_repo(tmp_repo)

    # 1. Valid self-dev creates isolated worktree branch, runs real pytest, emits diff + Review + Approval
    prop = propose_self_dev_worktree(
        temp_db_session,
        workspace_id=ws_id,
        user_id=uid,
        prompt="تحسين توثيق المشروع",
        target_rel_path="docs/improvement.md",
        new_content="# Platform Enhancement\n",
        repo_root_override=tmp_repo,
    )
    temp_db_session.commit()
    assert prop["status"] == "awaiting_approval"
    assert prop["branch_name"].startswith("selfdev/")
    assert prop["tests_passed"] is True
    assert "Platform Enhancement" in prop["diff_text"]
    assert prop["review_item_id"] and prop["approval_id"]

    # Main branch must stay completely untouched
    main_head_files = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "main"],
        cwd=str(tmp_repo),
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "docs/improvement.md" not in main_head_files

    approved = decide_self_dev_proposal(
        temp_db_session,
        workspace_id=ws_id,
        user_id=uid,
        proposal_id=prop["id"],
        approve=True,
        commit_in_worktree=True,
    )
    assert approved["status"] == "merged"

    # 2. Rejected proposal removes worktree & branch cleanly
    prop2 = propose_self_dev_worktree(
        temp_db_session,
        workspace_id=ws_id,
        user_id=uid,
        prompt="تجربة أخرى للمرفوض",
        target_rel_path="docs/other.md",
        new_content="other",
        repo_root_override=tmp_repo,
    )
    temp_db_session.commit()
    rejected = decide_self_dev_proposal(
        temp_db_session,
        workspace_id=ws_id,
        user_id=uid,
        proposal_id=prop2["id"],
        approve=False,
    )
    assert rejected["status"] == "rejected"
    assert not Path(prop2["worktree_path"]).exists()

    # 3. §0 Protection hard-blocks forbidden files (explicit path & inside prompt)
    for forbidden in (
        "backend/app/captcha/solver.py",
        "backend/app/services/captcha_inject.py",
        "browser-worker/worker.py",
        "backend/app/config.py",
        ".env",
        "accounts.xlsx",
    ):
        blk1 = propose_self_dev_worktree(
            temp_db_session,
            workspace_id=ws_id,
            user_id=uid,
            prompt="تعديل ملف نظامي",
            target_rel_path=forbidden,
            repo_root_override=tmp_repo,
        )
        blk2 = propose_self_dev_worktree(
            temp_db_session,
            workspace_id=ws_id,
            user_id=uid,
            prompt=f"عدل {forbidden} الآن",
            repo_root_override=tmp_repo,
        )
        assert blk1["status"] == "blocked" and blk1["branch_name"] == ""
        assert blk2["status"] == "blocked" and blk2["branch_name"] == ""


def test_hermes_aider_repo_map_dev_tools_mentions_architect_cost_cron_and_plugins(
    owner_session: dict,
    temp_db_session: Session,
    tmp_path: Path,
):
    client = owner_session["client"]
    headers = owner_session["headers"]
    ws_id = owner_session["data"]["workspace"]["id"]

    # 1. Repo map endpoint
    rmap = client.get("/api/dev/repo-map?query=memory", headers=headers)
    assert rmap.status_code == 200
    assert rmap.json()["total_files_indexed"] >= 5 and rmap.json()["map_text"]

    # 2. /test & /lint on temp repo + opt-in auto commit guards
    tmp_repo = tmp_path / "dev_repo"
    _init_temp_git_repo(tmp_repo)
    chk_test = run_workspace_checks(action="test", workspace_dir=tmp_repo)
    assert chk_test["ok"] is True

    # Auto-commit refuses without opt-in and refuses on main
    assert opt_in_auto_git_commit(repo_dir=tmp_repo, commit_message="x", allow=False)["committed"] is False
    (tmp_repo / "app_mod.py").write_text("def add(a, b):\n    return a + b + 0\n", encoding="utf-8")
    on_main = opt_in_auto_git_commit(repo_dir=tmp_repo, commit_message="x", allow=True)
    assert on_main["committed"] is False and "main" in on_main["reason"]

    subprocess.run(["git", "checkout", "-b", "feat/ok"], cwd=str(tmp_repo), check=True, capture_output=True)
    on_feat = opt_in_auto_git_commit(repo_dir=tmp_repo, commit_message="safe commit", allow=True)
    assert on_feat["committed"] is True and on_feat["branch"] == "feat/ok"

    # 3. @-mentions expansion & secret path blocking
    ctx, meta = expand_at_mentions("Check @file:app_mod.py @tree @file:.env", workspace_dir=str(tmp_repo))
    assert "def add(a, b):" in ctx
    assert any(m["mention"] == "@file:.env" and m["status"] == "blocked" for m in meta)

    # 4. Architect mode exists while delegate stays default
    modes = client.get("/api/modes", headers=headers).json()["modes"]
    keys = {m["key"]: m for m in modes}
    assert "architect" in keys
    assert keys["delegate"]["is_default"] is True

    # 5. Session cost endpoint
    cost = client.get("/api/stats/session-cost", headers=headers)
    assert cost.status_code == 200 and "cost_usd" in cost.json()

    # 6. Cron trigger multi-channel delivery (explicitly enabled channels)
    for ch in ("telegram", "whatsapp"):
        assert client.put(
            f"/api/messaging/channels/{ch}",
            json={"enabled": True, "config": {"bot_username": f"cron_{ch}"}},
            headers=headers,
        ).status_code == 200
    trig = AutomationTrigger(
        workspace_id=ws_id,
        name="Morning Multi-Channel Digest",
        trigger_type="schedule",
        cron_expr="0 8 * * *",
        action_prompt="Daily check",
        enabled=True,
    )
    temp_db_session.add(trig)
    temp_db_session.commit()
    fire_trigger(
        temp_db_session,
        trig,
        reason="cron_test",
        payload={"delivery_channels": ["telegram", "whatsapp"]},
    )
    temp_db_session.commit()
    delivered = temp_db_session.query(MessagingEventLog).filter(MessagingEventLog.workspace_id == ws_id).all()
    assert {"telegram", "whatsapp"}.issubset({d.channel for d in delivered})

    # 7. Commit-pinned plugin catalog
    cat = client.get("/api/plugins/catalog", headers=headers).json()["catalog"]
    assert len(cat) >= 3
    slug = cat[0]["slug"]
    assert len(cat[0]["pinned_commit"]) >= 12
    inst = client.post(f"/api/plugins/catalog/{slug}/install", headers=headers)
    assert inst.status_code == 200 and inst.json()["plugin"]["slug"] == slug


def test_manus_projects_wide_research_browser_mode_grok_and_eval_harness(owner_session: dict):
    client = owner_session["client"]
    headers = owner_session["headers"]

    # 1. Persistent Project with sandbox directory
    proj = client.post(
        "/api/projects",
        json={"name": "مشروع التجارة", "instructions_md": "التزم بالتوثيق", "autonomy_mode": "autonomous"},
        headers=headers,
    )
    assert proj.status_code == 200
    assert Path(proj.json()["project"]["sandbox_dir"]).is_dir()

    # 2. Wide Research fan-out <= 20 items & validation above 20
    wr = client.post(
        "/api/research/wide",
        json={"query": "مقارنة أطر بايثون", "items": ["FastAPI", "Django", "Litestar", "Flask"]},
        headers=headers,
    )
    assert wr.status_code == 200
    job = wr.json()["job"]
    assert job["progress_done"] == 4 and job["status"] == "done" and job["artifact_id"]

    too_many = client.post(
        "/api/research/wide",
        json={"query": "test", "items": [f"item{i}" for i in range(21)]},
        headers=headers,
    )
    assert too_many.status_code == 422

    # 3. Cloud vs Local Browser Operator toggle
    b1 = client.put("/api/browser/mode", json={"mode": "local_operator"}, headers=headers)
    assert b1.status_code == 200 and b1.json()["mode"] == "local_operator"
    b2 = client.get("/api/browser/mode", headers=headers)
    assert b2.json()["mode"] == "local_operator"

    # 4. Grok (xAI) provider configured cleanly in YAML catalogs
    repo_root = Path(__file__).resolve().parents[2]
    p_ex = yaml.safe_load((repo_root / "config" / "providers.example.yaml").read_text(encoding="utf-8"))
    p_v3 = yaml.safe_load((repo_root / "config" / "providers-v3.example.yaml").read_text(encoding="utf-8"))
    m_cfg = yaml.safe_load((repo_root / "config" / "models.yaml").read_text(encoding="utf-8"))
    assert any(p.get("id") == "xai" and p.get("auth") == "env:XAI_API_KEY" for p in p_ex["providers"])
    assert any(p.get("id") == "xai" for p in p_v3["providers"])
    assert "grok-3" in m_cfg.get("pricing_per_1m_usd", {})

    # 5. Eval Harness score & delta across runs
    ev1 = client.post("/api/evals/run", headers=headers)
    assert ev1.status_code == 200
    r1 = ev1.json()["run"]
    assert r1["score"] == 100.0 and r1["passed_count"] == r1["total_count"]
    ev2 = client.post("/api/evals/run", headers=headers)
    assert ev2.status_code == 200 and ev2.json()["run"]["delta_vs_prev"] == 0.0
