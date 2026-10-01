"""V2 UI smoke checks against static files and mocked APIs; never calls cloud APIs."""
import asyncio
import copy
import functools
import json
import os
import threading
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from browser_support import browser_launch_options
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parent.parent
CREATE_KEY = "charles-monitor.acp.create.pending"
STOP_KEY = "charles-monitor.acp.stop.pending"


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?",1)[0] == "/":
            self.path = "/static/index.html"
        super().do_GET()

    def log_message(self, *args):
        pass


async def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    now = datetime.now(timezone.utc).isoformat()
    task = {"id": "ACP:running-1", "kind": "ACP", "name": "charles running", "cluster": "modeltoolchain-test", "state": "running", "replicas": 1, "gpu_count": 1, "can_stop": True, "updated_at": now, "metrics": {}}
    waiting = {**task, "id": "ACP:waiting-1", "name": "charles waiting", "state": "WAIT_QUOTA", "state_label": "等待配额", "created_at": now, "observed_at": now}
    snapshot = {"status": "ok", "connected": True, "updated_at": now, "interval_seconds": 60, "tasks": [task, {**task, "id": "CCI:readonly", "kind": "CCI", "name": "charles CCI", "can_stop": True}], "waiting_tasks": [waiting], "clusters": [], "source_status": {"CCI": {"status": "ok"}, "ACP": {"status": "ok"}, "CCI_queue": {"status": "ok", "updated_at": now}, "ACP_queue": {"status": "ok", "updated_at": now}}}
    calls = []
    create_fails = False
    stop_fails = False
    create_timeout = False
    create_expired = False
    slow_options = True
    slow_preview = True

    async def api(route):
        nonlocal create_fails, stop_fails, create_timeout, create_expired, slow_options, slow_preview
        request = route.request
        path = request.url.split("/api/", 1)[1].split("?", 1)[0]
        body = request.post_data_json if request.method == "POST" else None
        calls.append((path, body))
        if request.method == "POST":
            assert request.headers.get("x-monitor-request") == "1"
            if path.startswith("acp/"):
                assert request.headers.get("content-type") == "application/json"
        if path == "state":
            data = copy.deepcopy(snapshot)
        elif path == "history":
            data = {"series": []}
        elif path == "acp/options":
            if slow_options:
                slow_options = False
                await asyncio.sleep(1.2)
            data = {"template": {"id": "template-1", "name": "charles template", "created_at": now}, "gpu_options": [1, 2, 4, 8], "default_gpus_per_replica": 1, "cluster_order": ["modeltoolchain_test", "modeltoolchain", "vigen"], "updated_at": now, "clusters": [{"id":"modeltoolchain-test","name":"modeltoolchain_test","gpu_options":[1,2,4,8]}, {"id":"vigen","name":"vigen","gpu_options":[1,8]}]}
        elif path == "acp/preview":
            if slow_preview:
                slow_preview = False
                await asyncio.sleep(1.2)
            assert set(body) == {"display_name", "replicas", "gpus_per_replica", "command", "cluster_id"}
            data = {**body, "preview_id": "preview-stable-1", "expires_at": "2099-01-01T00:00:00Z", "template": {"id": "template-1", "name": "charles template"}, "selection_mode": "manual" if body["cluster_id"] else "auto", "cluster": {"id": body["cluster_id"] or "cluster-1", "name": body["cluster_id"] or "modeltoolchain-test"}, "total_gpus": body["replicas"] * body["gpus_per_replica"], "vcpu_per_replica": 12, "memory_gib_per_replica": 96, "command": body["command"] or "sleep inf", "will_queue": True, "message": "当前资源不足，将进入队列。"}
            data['capacity_checks']=[{'cluster_id':'modeltoolchain-test','cluster_name':'modeltoolchain_test',
                'quota_sufficient':True,'node_check':{'status':'insufficient','message':'节点 GPU / CPU / 内存可容纳 0 个副本，需要 1 个副本。'}}]
        elif path == "acp/create":
            assert body == {"preview_id": "preview-stable-1"}
            assert await request.frame.evaluate("key => JSON.parse(localStorage.getItem(key))", CREATE_KEY) == body, "persist identity before create"
            if create_expired:
                create_expired = False
                await route.fulfill(status=409, json={"error": "旧预览已过期", "code": "preview_expired"})
                return
            if create_timeout:
                create_timeout = False
                await asyncio.sleep(1.5)
            if create_fails:
                create_fails = False
                await route.abort("failed")
                return
            data = {"id": "ACP:new-1", "name": "charles new", "state": "WAIT_QUOTA", "message": "已提交创建，等待采集。"}
        elif path == "acp/stop":
            assert body["request_id"]
            assert await request.frame.evaluate("key => JSON.parse(localStorage.getItem(key))", STOP_KEY) == body, "persist identity before stop"
            if stop_fails:
                stop_fails = False
                await route.abort("failed")
                return
            data = {"id": body["task_id"], "name": body["display_name"], "state": "SUSPENDING", "message": "已提交停止。"}
        elif path == "refresh":
            data = {"message": "已刷新"}
        else:
            raise AssertionError(f"Unexpected API path: {path}")
        await route.fulfill(json=data)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(**browser_launch_options())
            context = await browser.new_context(viewport={"width": 1440, "height": 1000})
            page = await context.new_page()
            await page.add_init_script("window.requestTimeouts = []; const originalTimeout = window.setTimeout; window.setTimeout = (fn, ms, ...args) => { window.requestTimeouts.push(ms); return originalTimeout(fn, ms === 90000 ? 1000 : ms, ...args); };")
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.route("**/api/**", api)
            await page.goto(f"http://127.0.0.1:{server.server_port}/#acp", wait_until="networkidle")
            await expect(page.locator("#acp-create-form")).to_be_visible()
            assert await page.locator("#acp-create-form input, #acp-create-form select, #acp-create-form textarea").count() == 5
            await expect(page.locator("#acp-name")).to_have_value("kill_before_notice_grh_charles_test")
            await expect(page.locator("#acp-cluster")).to_have_value("")
            await page.locator("#acp-gpus").select_option("4")
            await page.locator("#acp-cluster").select_option("vigen")
            assert await page.locator("#acp-gpus option").all_text_contents() == ["1 张", "8 张"]
            await expect(page.locator("#acp-gpus")).to_have_value("1")
            await page.locator("#refresh-button").click()
            await expect(page.locator("#acp-cluster")).to_have_value("vigen")
            await page.locator("#acp-preview-button").click()
            await expect(page.locator("#acp-create-dialog")).to_be_visible()
            await expect(page.locator("#acp-preview-details")).to_contain_text("指定集群")
            await expect(page.locator("#acp-preview-details")).to_contain_text("节点 GPU / CPU / 内存可容纳 0 个副本")
            assert [body for path,body in calls if path == "acp/preview"][-1]["cluster_id"] == "vigen"
            await page.locator("#acp-create-cancel").click()
            await page.locator("#acp-cluster").select_option("")
            await page.locator("#acp-replicas").fill("3")
            await page.locator("#acp-gpus").select_option("4")
            await expect(page.locator("#acp-total")).to_have_text("12 张 GPU")
            await page.locator("#refresh-button").click()
            await expect(page.locator("#acp-replicas")).to_have_value("3")
            await page.locator("#acp-preview-button").click()
            await expect(page.locator("#acp-create-dialog")).to_be_visible()
            assert await page.evaluate("window.requestTimeouts.filter(ms => ms === 60000).length >= 2"), "options and preview have 60s read budgets"
            for text in ["template-1", "charles template", "modeltoolchain-test", "12", "96", "sleep inf", "当前资源不足"]:
                assert text in await page.locator("#acp-preview-details").inner_text()
            create_fails = True
            await page.locator("#acp-create-confirm").click()
            await expect(page.locator("#acp-create-message")).to_contain_text("结果未知")
            assert await page.evaluate("key => JSON.parse(localStorage.getItem(key))", CREATE_KEY) == {"preview_id": "preview-stable-1"}
            await page.locator("#acp-create-cancel").click()
            await page.reload(wait_until="networkidle")
            await expect(page.locator("#acp-preview-button")).to_be_disabled()
            await page.locator("#acp-create-resume").click()
            await expect(page.locator("#acp-create-dialog")).to_be_visible()
            await expect(page.locator("#acp-create-confirm")).to_be_enabled()
            await page.locator("#acp-create-confirm").click()
            await expect(page.locator("#acp-create-dialog")).not_to_be_visible()
            assert len([call for call in calls if call[0] == "acp/create"]) == 2
            assert await page.evaluate("key => localStorage.getItem(key)", CREATE_KEY) is None
            await page.locator('.nav-item[href="#tasks"]').click()
            await page.locator("#acp-stop-selected").click()
            await expect(page.locator("#acp-stop-target")).to_contain_text("ACP:running-1")
            await expect(page.locator("#acp-stop-target")).to_contain_text("charles running")
            stop_fails = True
            await page.locator("#acp-stop-confirm").click()
            await expect(page.locator("#acp-stop-message")).to_contain_text("结果未知")
            saved_stop = await page.evaluate("key => JSON.parse(localStorage.getItem(key))", STOP_KEY)
            assert set(saved_stop) == {"request_id", "task_id", "display_name"}
            await page.reload(wait_until="networkidle")
            await expect(page.locator("#acp-stop-selected")).to_be_disabled()
            await page.locator("#acp-stop-resume").click()
            await expect(page.locator("#acp-stop-target")).to_contain_text(saved_stop["task_id"])
            await page.locator("#acp-stop-confirm").click()
            await expect(page.locator("#acp-stop-dialog")).not_to_be_visible()
            assert [body for path, body in calls if path == "acp/stop"][-1]["task_id"] == "ACP:running-1"
            stop_bodies = [body for path, body in calls if path == "acp/stop"]
            assert stop_bodies[0] == stop_bodies[1], "stop retry must preserve exact request identity"
            assert await page.evaluate("key => localStorage.getItem(key)", STOP_KEY) is None
            assert await page.locator(".task-card button").count() == 0, "no nested task-card actions"
            await page.locator(".task-card").filter(has_text="charles CCI").click()
            await expect(page.locator("#acp-stop-selected")).not_to_be_visible()
            await page.locator('.nav-item[href="#waiting"]').click()
            await page.locator("#waiting-body button").click()
            await expect(page.locator("#acp-stop-target")).to_contain_text("ACP:waiting-1")
            await page.locator("#acp-stop-cancel").click()
            snapshot["waiting_tasks"][0]["state"] = "SUSPENDING"
            await page.locator("#refresh-button").click()
            await expect(page.locator("#waiting-body")).not_to_contain_text("停止中")
            await expect(page.locator("#waiting-body button")).to_have_count(0)
            snapshot["waiting_tasks"][0]["state"] = "STARTING"
            snapshot["source_status"]["ACP_queue"]["status"] = "error"
            await page.locator("#refresh-button").click()
            await expect(page.locator("#waiting-notice")).to_contain_text("上次等待队列")
            await expect(page.locator("#waiting-body button")).to_be_disabled()
            snapshot["source_status"]["ACP_queue"]["status"] = "ok"
            await page.locator('.nav-item[href="#tasks"]').click()
            await page.locator(".task-card").filter(has_text="charles running").click()
            for conditions in [
                {"connected": False}, {"status": "disconnected"}, {"status": "error"},
                {"updated_at": "2020-01-01T00:00:00Z"},
                {"source_status": {"CCI": {"status": "ok"}, "ACP": {"status": "ok", "observed_at": "2020-01-01T00:00:00Z"}}},
            ]:
                before = copy.deepcopy(snapshot)
                snapshot.update(conditions)
                await page.locator("#refresh-button").click()
                await expect(page.locator("#waiting-notice")).to_contain_text("旧")
                await expect(page.locator("#waiting-body button")).to_be_disabled()
                await expect(page.locator("#acp-stop-selected")).to_be_disabled()
                snapshot.clear()
                snapshot.update(before)
            await page.locator("#refresh-button").click()
            await expect(page.locator("#acp-stop-selected")).to_be_enabled()
            snapshot["waiting_tasks"] = []
            snapshot["tasks"].append({**waiting, "state": "running"})
            await page.locator("#refresh-button").click()
            await expect(page.locator("#waiting-body")).to_contain_text("当前没有等待或启动中的 CCI / ACP 任务")
            await expect(page.locator(".task-card").filter(has_text="charles waiting")).to_be_visible()
            snapshot["source_status"]["ACP_queue"]["status"] = "error"
            await page.locator("#refresh-button").click()
            await expect(page.locator("#waiting-body")).to_contain_text("等待任务数量暂时未知")
            # Mixed owners and services stay visible; other users and all CCI entries are read-only.
            snapshot["source_status"]["ACP_queue"] = {"status":"ok", "updated_at":now}
            snapshot["waiting_tasks"] = [
                {**waiting, "owner":"gongruihao"},
                {**waiting, "id":"ACP:alice", "name":"alice training", "owner":"alice", "can_stop":True},
                {**waiting, "id":"CCI:bob", "kind":"CCI", "name":"bob container", "owner":"bob", "state":"PROGRESSING", "state_label":"启动中", "can_stop":True},
            ]
            await page.locator('.nav-item[href="#waiting"]').click()
            await page.locator("#refresh-button").click()
            await expect(page.locator("#waiting-count")).to_have_text("3")
            await expect(page.locator("#waiting-summary")).to_have_text("CCI 1 · ACP 2")
            await expect(page.locator("#waiting-body")).to_contain_text("alice training")
            await expect(page.locator("#waiting-body")).to_contain_text("bob container")
            assert await page.locator("#waiting-body button").count() == 1
            await expect(page.locator("#waiting-body tr").filter(has_text="alice training")).to_contain_text("只读")
            await expect(page.locator("#waiting-body tr").filter(has_text="bob container")).to_contain_text("只读")
            snapshot["source_status"]["CCI_queue"]["status"] = "error"
            await page.locator("#refresh-button").click()
            await expect(page.locator("#waiting-count")).to_have_text("—")
            await expect(page.locator("#waiting-summary")).to_have_text("CCI 未知 · ACP 2")
            await expect(page.locator("#waiting-body tr").filter(has_text="bob container")).to_contain_text("旧数据")
            await expect(page.locator("#waiting-body button")).to_be_enabled()
            snapshot["source_status"]["CCI_queue"]["status"] = "ok"
            await page.locator("#refresh-button").click()
            screenshot_dir = os.environ.get("V2_SCREENSHOT_DIR")
            if screenshot_dir:
                Path(screenshot_dir).mkdir(parents=True, exist_ok=True)
                await page.screenshot(path=str(Path(screenshot_dir) / "v2-desktop.png"), full_page=True)
            await page.set_viewport_size({"width": 390, "height": 844})
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "mobile horizontal overflow"
            if screenshot_dir:
                await page.screenshot(path=str(Path(screenshot_dir) / "v2-mobile.png"), full_page=True)
            await page.locator('.nav-item[href="#acp"]').click()
            await page.locator("#acp-command").fill("printf '<img src=x onerror=window.untrustedExecuted=true>'")
            await page.locator("#acp-preview-button").click()
            await expect(page.locator("#acp-create-dialog")).to_be_visible()
            assert await page.locator("#acp-preview-details img").count() == 0
            assert await page.evaluate("window.untrustedExecuted === undefined")
            assert await page.locator("#acp-create-dialog").evaluate("e => e.scrollWidth <= e.clientWidth"), "dialog horizontal overflow"
            if screenshot_dir:
                await page.screenshot(path=str(Path(screenshot_dir) / "v2-mobile-confirm.png"))
            create_timeout = True
            await page.locator("#acp-create-confirm").click()
            await expect(page.locator("#acp-create-confirm")).to_be_disabled()
            await expect(page.locator("#acp-create-message")).to_contain_text("结果未知")
            await page.locator("#acp-create-confirm").click()
            await expect(page.locator("#acp-create-dialog")).not_to_be_visible()
            peer = await page.context.new_page()
            await peer.route("**/api/**", api)
            await peer.goto(page.url, wait_until="networkidle")
            await page.evaluate("([key, value]) => localStorage.setItem(key, JSON.stringify(value))", [CREATE_KEY, {"preview_id": "preview-stable-1"}])
            await expect(peer.locator("#acp-preview-button")).to_be_disabled()
            await peer.locator("#acp-create-resume").click()
            create_expired = True
            await peer.locator("#acp-create-confirm").click()
            await expect(peer.locator("#acp-create-message")).to_contain_text("过期")
            await expect(peer.locator("#acp-preview-button")).to_be_enabled()
            assert await peer.evaluate("key => localStorage.getItem(key)", CREATE_KEY) is None
            await peer.locator("#acp-create-cancel").click()
            snapshot["source_status"]["ACP_queue"]["status"] = "ok"
            await page.locator("#refresh-button").click()
            await peer.evaluate("([key, value]) => localStorage.setItem(key, JSON.stringify(value))", [STOP_KEY, saved_stop])
            await expect(page.locator("#acp-stop-resume")).to_be_visible()
            await expect(page.locator("#acp-stop-selected")).to_be_disabled()
            await peer.evaluate("key => localStorage.removeItem(key)", STOP_KEY)
            await expect(page.locator("#acp-stop-resume")).not_to_be_visible()
            await expect(page.locator("#acp-stop-selected")).to_be_enabled()
            await peer.close()
            assert not errors, errors
            await browser.close()
            print(json.dumps({"result": "passed", "checks": ["five create inputs with optional cluster", "GPU total", "form survives poll", "preview", "safe create retry", "stop identity confirmation and stable retry", "identity persisted before send", "create and stop survive reload", "explicit rejection clears recovery", "cross-tab create and stop protection", "60s options and preview budget", "CCI read-only", "waiting stop", "stopping excluded", "mixed service all-user queues", "foreign tasks read-only", "per-service queue freshness", "disconnected/error/aged source stop disabled", "waiting to running", "empty vs unknown", "390px page and dialog", "untrusted command rendered as text", "create timeout unknown", "no JS errors"]}, ensure_ascii=False))
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    asyncio.run(main())
