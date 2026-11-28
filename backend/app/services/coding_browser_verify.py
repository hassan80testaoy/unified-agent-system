from __future__ import annotations

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from typing import Any
from urllib.request import urlopen

import websockets


_BROWSER_CANDIDATES = [
    os.environ.get("BROWSER_BIN", ""),
    shutil.which("chrome") or "",
    shutil.which("google-chrome") or "",
    shutil.which("chromium") or "",
    shutil.which("chromium-browser") or "",
    shutil.which("msedge") or "",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]


def find_browser_binary() -> str | None:
    for candidate in _BROWSER_CANDIDATES:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        super().do_GET()


def _start_static_server(directory: Path) -> tuple[ThreadingHTTPServer, int, threading.Thread]:
    handler = partial(_QuietHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = int(server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port, thread


def _wait_for_cdp_ws_url(cdp_port: int, timeout_s: float = 12.0) -> str:
    deadline = time.time() + timeout_s
    last_err = ""
    while time.time() < deadline:
        try:
            with urlopen(f"http://127.0.0.1:{cdp_port}/json/list", timeout=1.5) as resp:
                pages = json.loads(resp.read().decode("utf-8"))
                for entry in pages:
                    if entry.get("type") == "page" and entry.get("webSocketDebuggerUrl"):
                        return str(entry["webSocketDebuggerUrl"])
        except Exception as exc:
            last_err = str(exc)
        time.sleep(0.15)
    raise RuntimeError(f"CDP did not become ready on port {cdp_port}: {last_err}")


async def _cdp_verify_session(
    ws_url: str,
    target_url: str,
    artifact_dir: Path,
    script_actions: list[dict[str, Any]] | None,
    wait_ms: int,
) -> dict[str, Any]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    console_errors: list[str] = []
    exceptions: list[str] = []
    console_logs: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    frame_paths: list[str] = []
    msg_id = 0
    pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
    load_event = asyncio.Event()

    async with websockets.connect(ws_url, max_size=20 * 1024 * 1024) as ws:

        async def reader() -> None:
            try:
                async for raw in ws:
                    data = json.loads(raw)
                    if "id" in data:
                        fut = pending.pop(int(data["id"]), None)
                        if fut and not fut.done():
                            fut.set_result(data)
                        continue
                    method = data.get("method", "")
                    params = data.get("params") or {}
                    if method == "Page.loadEventFired":
                        load_event.set()
                    elif method == "Runtime.consoleAPICalled":
                        ctype = str(params.get("type", "log"))
                        args = params.get("args") or []
                        text = " ".join(
                            str(a.get("value") if "value" in a else a.get("description", ""))
                            for a in args
                        ).strip()
                        console_logs.append({"type": ctype, "text": text})
                        if ctype in ("error", "assert"):
                            console_errors.append(text or f"console.{ctype}")
                    elif method == "Runtime.exceptionThrown":
                        details = params.get("exceptionDetails") or {}
                        exc_obj = details.get("exception") or {}
                        desc = str(
                            exc_obj.get("description")
                            or details.get("text")
                            or "Uncaught JS exception"
                        )
                        exceptions.append(desc)
                        console_errors.append(desc)
                    elif method == "Log.entryAdded":
                        entry = params.get("entry") or {}
                        if entry.get("level") == "error":
                            msg = str(entry.get("text") or "Browser log error")
                            console_errors.append(msg)
            except Exception:
                return

        def _record_eval_error(res: dict[str, Any]) -> None:
            exc_details = res.get("exceptionDetails")
            if exc_details:
                exc_obj = exc_details.get("exception") or {}
                desc = str(
                    exc_obj.get("description")
                    or exc_details.get("text")
                    or "Evaluation exception"
                )
                if desc not in exceptions:
                    exceptions.append(desc)
                if desc not in console_errors:
                    console_errors.append(desc)

        async def call(method: str, params: dict[str, Any] | None = None, timeout: float = 8.0) -> dict[str, Any]:
            nonlocal msg_id
            msg_id += 1
            cid = msg_id
            fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
            pending[cid] = fut
            await ws.send(json.dumps({"id": cid, "method": method, "params": params or {}}))
            resp = await asyncio.wait_for(fut, timeout=timeout)
            return resp.get("result") or {}

        reader_task = asyncio.create_task(reader())
        try:
            await call("Page.enable")
            await call("Runtime.enable")
            await call("Log.enable")
            await call(
                "Emulation.setDeviceMetricsOverride",
                {"width": 1280, "height": 800, "deviceScaleFactor": 1, "mobile": False},
            )

            started_at = datetime.now(timezone.utc).isoformat()
            await call("Page.navigate", {"url": target_url})
            try:
                await asyncio.wait_for(load_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass
            await asyncio.sleep(max(wait_ms, 250) / 1000.0)
            steps.append({"action": "navigate", "url": target_url, "at": datetime.now(timezone.utc).isoformat()})

            # Frame 1: initial rendered screen
            shot1 = await call("Page.captureScreenshot", {"format": "png"})
            frame1_path = artifact_dir / f"frame_{ts}_1_initial.png"
            frame1_path.write_bytes(base64.b64decode(shot1["data"]))
            frame_paths.append(str(frame1_path))

            # Interactive exercise (custom script_actions or default form/button/input check)
            if script_actions:
                for idx, act in enumerate(script_actions, start=1):
                    expr = str(act.get("js") or "")
                    if expr:
                        eval_res = await call(
                            "Runtime.evaluate",
                            {"expression": expr, "awaitPromise": True, "returnByValue": True},
                        )
                        _record_eval_error(eval_res)
                        steps.append(
                            {
                                "action": act.get("name", f"step_{idx}"),
                                "result": (eval_res.get("result") or {}).get("value"),
                                "at": datetime.now(timezone.utc).isoformat(),
                            }
                        )
                        await asyncio.sleep(0.2)
            else:
                auto_js = r"""
                (() => {
                  const summary = { clicked: false, submitted: false, itemAdded: false };
                  const input = document.querySelector('input[type="text"], input:not([type]), textarea');
                  const btn = document.querySelector('button[type="submit"], form button, button#add-btn, button');
                  if (input) {
                    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                    if (setter) setter.call(input, 'مهمة اختبار المتصفح');
                    else input.value = 'مهمة اختبار المتصفح';
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                  }
                  if (btn) {
                    btn.click();
                    summary.clicked = true;
                  }
                  summary.textAfter = (document.body?.innerText || '').slice(0, 500);
                  summary.itemAdded = summary.textAfter.includes('مهمة اختبار المتصفح');
                  return summary;
                })()
                """
                eval_res = await call(
                    "Runtime.evaluate",
                    {"expression": auto_js, "awaitPromise": True, "returnByValue": True},
                )
                _record_eval_error(eval_res)
                steps.append(
                    {
                        "action": "auto_interact",
                        "result": (eval_res.get("result") or {}).get("value"),
                        "at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                await asyncio.sleep(0.25)

            # Collect DOM summary + capture final screenshot
            dom_eval = await call(
                "Runtime.evaluate",
                {
                    "expression": r"""(() => ({
                        title: document.title || '',
                        textLength: (document.body?.innerText || '').trim().length,
                        buttons: document.querySelectorAll('button').length,
                        inputs: document.querySelectorAll('input, textarea').length,
                        htmlPreview: (document.body?.innerHTML || '').slice(0, 400)
                    }))()""",
                    "returnByValue": True,
                },
            )
            dom_summary = (dom_eval.get("result") or {}).get("value") or {}

            shot_final = await call("Page.captureScreenshot", {"format": "png"})
            screenshot_path = artifact_dir / f"screenshot_{ts}.png"
            png_bytes = base64.b64decode(shot_final["data"])
            screenshot_path.write_bytes(png_bytes)
            frame_paths.append(str(screenshot_path))

            finished_at = datetime.now(timezone.utc).isoformat()
            recording_path = artifact_dir / f"recording_{ts}.json"
            recording_bundle = {
                "url": target_url,
                "viewport": {"width": 1280, "height": 800},
                "started_at": started_at,
                "finished_at": finished_at,
                "steps": steps,
                "console_errors": console_errors,
                "exceptions": exceptions,
                "console_logs": console_logs,
                "dom_summary": dom_summary,
                "screenshot_path": str(screenshot_path),
                "frame_paths": frame_paths,
            }
            recording_path.write_text(json.dumps(recording_bundle, ensure_ascii=False, indent=2), encoding="utf-8")

            return {
                "ok": len(console_errors) == 0 and len(exceptions) == 0 and len(png_bytes) > 100,
                "console_errors": console_errors,
                "exceptions": exceptions,
                "console_logs": console_logs,
                "dom_summary": dom_summary,
                "screenshot_path": str(screenshot_path),
                "recording_path": str(recording_path),
                "frame_paths": frame_paths,
                "steps": steps,
            }
        finally:
            reader_task.cancel()


def verify_app_in_real_browser(
    workspace_dir: Path,
    artifact_dir: Path,
    entry_file: str = "index.html",
    script_actions: list[dict[str, Any]] | None = None,
    wait_ms: int = 400,
) -> dict[str, Any]:
    """Serve `workspace_dir` locally, launch headless Chrome/Edge via CDP, inspect console errors, and capture PNG + recording."""
    browser_bin = find_browser_binary()
    if not browser_bin:
        return {
            "ok": False,
            "error": "no_headless_browser_installed",
            "console_errors": ["No Chrome/Edge/Chromium binary found for real browser verification."],
            "exceptions": [],
            "screenshot_path": None,
            "recording_path": None,
        }

    entry = workspace_dir / entry_file
    if not entry.exists():
        # Pick any HTML file if index.html is nested (e.g. public/index.html)
        html_files = list(workspace_dir.rglob("*.html"))
        if html_files:
            entry = html_files[0]
            rel_url = entry.relative_to(workspace_dir).as_posix()
        else:
            return {
                "ok": False,
                "error": "no_html_entry_found",
                "console_errors": [f"No {entry_file} found in {workspace_dir}"],
                "exceptions": [],
                "screenshot_path": None,
                "recording_path": None,
            }
    else:
        rel_url = entry_file

    server, http_port, _server_thread = _start_static_server(workspace_dir)
    cdp_port = _free_port()
    user_data_dir = Path(tempfile.mkdtemp(prefix="uas_cdp_"))
    proc: subprocess.Popen[bytes] | None = None

    try:
        cmd = [
            browser_bin,
            "--headless=new",
            f"--remote-debugging-port={cdp_port}",
            f"--user-data-dir={user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
            "--disable-extensions",
            "--window-size=1280,800",
        ]
        if os.name != "nt":
            cmd.append("--no-sandbox")
        cmd.append("about:blank")

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        ws_url = _wait_for_cdp_ws_url(cdp_port, timeout_s=12.0)
        target_url = f"http://127.0.0.1:{http_port}/{rel_url.lstrip('/')}"

        def _run() -> dict[str, Any]:
            return asyncio.run(
                _cdp_verify_session(
                    ws_url=ws_url,
                    target_url=target_url,
                    artifact_dir=artifact_dir,
                    script_actions=script_actions,
                    wait_ms=wait_ms,
                )
            )

        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_run).result(timeout=35)
    finally:
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=4)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        try:
            server.shutdown()
            server.server_close()
        except Exception:
            pass
        shutil.rmtree(user_data_dir, ignore_errors=True)
