"""Local read-only recruitment workbench and deterministic task planner."""

import argparse
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sys
from dataclasses import replace
from urllib.parse import unquote, urlsplit
import os
from http.cookies import SimpleCookie, CookieError
from uuid import uuid4


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
AGENT = ROOT / "boss-agent" / "src"
sys.path.insert(0, str(AGENT))

from boss_agent.semantic_task_parser import execution_matches, parse_task_with_metadata
from boss_agent.job_agent import load_local_llm_env
from boss_agent.task_planner import plan_recruitment_task
from boss_agent.recommend_preview import load_preview_task
from boss_agent.task_snapshot import anchors_complete, public_snapshot
from boss_agent.task_schema import BOSS_PAGE_FILTERS, validate_boss_page_filters
from boss_agent.boss_major_filters import BOSS_MAJOR_GROUPS, BOSS_MAJOR_SHORTCUTS
from integration import Workbench
from reviews import BlindStudy, ReviewAccessError, ReviewConflict


RUN_REVIEW = re.compile(r"/api/runs/([a-f0-9]{12})/(candidates|differences|reviews|education-reviews)(?:/([^/]+))?")
STUDY_REVIEW = re.compile(r"/api/review-study(?:/([a-f0-9]{32})(?:/(annotate|adjudicate))?)?")
PLAN_READ = re.compile(r"/api/plans/([a-f0-9]{32})")


def local_reports():
    reports = []
    for path in sorted(ROOT.glob("live_readonly_screen_*/screening.json"), reverse=True):
        if not re.fullmatch(r"live_readonly_screen_\d{8}_\d{6}", path.parent.name):
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8-sig"))
            rows = document.get("rows", document.get("anonymous_rows"))
            if not isinstance(rows, list) or not rows or not all(
                isinstance(row, dict) and re.fullmatch(r"R\d{2,}", str(row.get("id", ""))) for row in rows
            ):
                continue
            if not any(row.get("identity") == "card_detail_verified" for row in rows):
                continue
        except (OSError, ValueError, AttributeError):
            continue
        reports.append({
            "stamp": path.parent.name.removeprefix("live_readonly_screen_").replace("_", " "),
            "data": document,
        })
    return reports


class Handler(BaseHTTPRequestHandler):
    def review_owner(self):
        owner = self.owner(required=False)
        if not owner:
            raise ReviewAccessError("请先打开工作台以建立会话")
        return owner

    def review_api(self, run, study, payload=None):
        owner = self.review_owner()
        workspace = self.server.workbench
        if study:
            service = getattr(self.server, "review_study", None)
            if service is None:
                raise ReviewAccessError("离线盲标未启用")
            key, endpoint = study.groups()
            if payload is None and endpoint is None:
                return service.read(owner, key)
            if payload is not None and key and endpoint:
                return service.write(owner, key, endpoint, payload)
        else:
            run_id, endpoint, candidate = run.groups()
            if payload is None and endpoint != "education-reviews" and (not candidate or endpoint == "candidates"):
                return workspace.reviews.read(owner, run_id, endpoint, unquote(candidate) if candidate else None)
            if payload is not None and endpoint == "reviews" and not candidate:
                return workspace.reviews.write(owner, run_id, payload)
            if payload is not None and endpoint == "education-reviews" and not candidate:
                return workspace.reviews.write_education(owner, run_id, payload)
        raise ValueError("复核接口或操作无效")

    def review_reply(self, run, study, payload=None):
        try:
            self.reply(self.review_api(run, study, payload))
        except ReviewAccessError as error:
            self.reply({"error": str(error)}, 403)
        except ReviewConflict as error:
            self.reply({"error": str(error)}, 409)
        except (TypeError, ValueError, KeyError, AttributeError) as error:
            self.reply({"error": str(error)}, 400)

    def owner(self, required=True):
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            token = cookie.get("workbench_owner")
        except CookieError:
            token = None
        if token and re.fullmatch(r"[a-f0-9]{32}", token.value):
            return sha256(token.value.encode("ascii")).hexdigest()
        if required:
            raise ValueError("请重新打开工作台以建立本地会话")
        return None

    @staticmethod
    def tab_revision(payload):
        tab, revision = payload.get("tab"), payload.get("generation")
        if (not isinstance(tab, str) or not re.fullmatch(r"[a-f0-9]{32}", tab)
                or type(revision) is not int or not 0 <= revision < 2**53):
            raise ValueError("页面版本无效")
        return tab, revision
    def local_host(self):
        port = self.server.server_port
        return self.headers.get("Host") in {f"127.0.0.1:{port}",f"localhost:{port}"}

    def reply(self, value, status=200):
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self.local_host():
            self.send_error(403)
            return
        path = urlsplit(self.path).path
        run, study = RUN_REVIEW.fullmatch(path), STUDY_REVIEW.fullmatch(path)
        if run or study:
            self.review_reply(run, study)
            return
        plan = PLAN_READ.fullmatch(path)
        if plan:
            try:
                owner = self.review_owner()
                snapshots = self.server.workbench.snapshots
                with snapshots.transaction() as db:
                    row = snapshots.read(db, owner, plan[1])
                self.reply({**self.plan_response(row), "job_id": row["job"]["job_id"],
                            "source_text": row["source_text"]})
            except ReviewAccessError as error:
                self.reply({"error": str(error)}, 403)
            except ValueError as error:
                self.reply({"error": str(error)}, 409)
            return
        if path == "/api/workspace":
            summary = self.server.workbench.overview(owner=self.owner(required=False))
            self.reply({**summary, "boss_page_filter_options": BOSS_PAGE_FILTERS,
                        "boss_major_groups": {**BOSS_MAJOR_GROUPS, "页面快捷选项": BOSS_MAJOR_SHORTCUTS},
                        "review_study_enabled": getattr(self.server, "review_study", None) is not None})
            return
        if path == "/api/browser":
            self.reply(self.server.workbench.browser_status())
            return
        if path == "/api/reports":
            self.reply([] if getattr(self.server.workbench, "mock_dir", None) is not None else local_reports())
            return
        if path == "/api/catalog":
            self.reply(json.loads((AGENT / "boss_agent" / "undergraduate_majors.json").read_text(encoding="utf-8")))
            return
        assets = {"/": ("index.html", "text/html"), "/motion.js": ("motion.js", "text/javascript"),
                  "/vendor/app.css": ("vendor/app.css", "text/css"),
                  "/vendor/favicon.svg": ("vendor/favicon.svg", "image/svg+xml"),
                  "/app.js": ("app.js", "text/javascript"), "/operations.js": ("operations.js", "text/javascript"),
                  "/reviews.js": ("reviews.js", "text/javascript"),
                  "/condition-editor.js": ("condition-editor.js", "text/javascript"),
                  "/task-workflow.js": ("task-workflow.js", "text/javascript")}
        if path not in assets:
            self.send_error(404)
            return
        filename, kind = assets[path]
        data = (HERE / filename).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{kind}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'self'")
        self.send_header("Cache-Control", "no-store")
        if path == "/" and not self.owner(required=False):
            self.send_header("Set-Cookie", f"workbench_owner={uuid4().hex}; Path=/; HttpOnly; SameSite=Strict; Max-Age=86400")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if not self.local_host():
            self.send_error(403)
            return
        plan_action = re.fullmatch(r"/api/plans/([a-f0-9]{32})/(confirm|revise|revoke|resize)", self.path)
        cancel_run = re.fullmatch(r"/api/runs/([A-Za-z0-9_-]{1,64})/cancel", self.path)
        run, study = RUN_REVIEW.fullmatch(self.path), STUDY_REVIEW.fullmatch(self.path)
        if not (plan_action or cancel_run or run or study) and self.path not in {"/api/plan","/api/jobs","/api/runs","/api/scheduler","/api/disposition","/api/plans/invalidate","/api/browser/connect","/api/job-policy","/api/rewrite"}:
            self.send_error(404)
            return
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != self.headers.get("Host"):
            self.reply({"error": "请从本地工作台页面提交"}, 403)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 20000:
                raise ValueError("任务内容过长或为空")
            payload = json.loads(self.rfile.read(length))
            workspace = self.server.workbench
            if self.path != "/api/plan":
                if self.headers.get("X-Workbench") != "local-ui":
                    raise ValueError("请从工作台提交操作")
                if run or study:
                    if not isinstance(payload, dict):
                        raise ValueError("复核请求必须是对象")
                    self.review_reply(run, study, payload)
                    return
                if plan_action or self.path == "/api/plans/invalidate":
                    self.reply(self.change_plan(workspace, plan_action, payload))
                    return
                if cancel_run:
                    self.reply(workspace.cancel(cancel_run[1], owner=self.owner()))
                    return
                if self.path == "/api/job-policy":
                    self.reply(workspace.save_job_policy(payload))
                    return
                if self.path == "/api/rewrite":
                    self.reply(workspace.rewrite_requirements(payload))
                    return
                if self.path == "/api/browser/connect":
                    self.reply(workspace.connect_browser())
                    return
                result = {
                    "/api/jobs": lambda: workspace.save_job(payload),
                    "/api/runs": lambda: workspace.enqueue(payload["action"], payload,
                                                          owner=self.owner() if payload["action"] in {"preview", "greet"} else None),
                    "/api/scheduler": lambda: workspace.scheduler(payload["enabled"]),
                    "/api/disposition": lambda: workspace.disposition(payload["id"], payload["status"]),
                }[self.path]()
                self.reply(result)
                return
            text, job = payload["request"], payload.get("job", "")
            record = None
            if "job_id" in payload:
                if self.headers.get("X-Workbench") != "local-ui":
                    raise ValueError("请从工作台提交操作")
                owner = self.owner()
                tab, generation = self.tab_revision(payload)
                record = workspace.snapshots.job(payload["job_id"])
                workspace.snapshots.invalidate(owner, tab, generation)
                job = record["job_name"]
            elif job not in workspace.overview()["published_jobs"]:
                raise ValueError("请选择已同步的 BOSS 岗位。")
            model_only = getattr(self.server, "model_only", False)
            if model_only and payload.get("force_rule") is True:
                parsed = parse_task_with_metadata(text, enabled=False)
            elif model_only:
                parsed = parse_task_with_metadata(text, enabled=True, select=True)
            elif getattr(workspace, "demo", False):
                parsed = parse_task_with_metadata(text, enabled=False)
            else:
                parsed = parse_task_with_metadata(text)
            if model_only and parsed.metadata["mode"] != "ai":
                if payload.get("force_rule") is not True:
                    # 模型没通过（漏条件/超时等）自动退到规则解析，条件仍可逐条核对
                    reason = str(parsed.metadata.get("fallback_reason") or "未启用模型")
                    parsed = parse_task_with_metadata(text, enabled=False)
                    parsed.metadata["fallback_reason"] = reason
            task = parsed.task
            if task.job and task.job != job:
                raise ValueError("所选岗位与招聘指令中的岗位不一致。")
            task = replace(task, job=job, boss_page_filters=validate_boss_page_filters(payload.get("boss_page_filters", {})))
            task = workspace.absorb_unresolved(task, payload.get("bonus_lines") or ())
            # 勾选“筛选后自动打招呼”时，按工作台开关补足联系意图与人数上限
            task = workspace.auto_greet_task(task, payload.get("auto_greet") is True)
            # 按岗位配置的判定规则（核心全满足 / 核心达标+加分项补足）
            job_key = payload.get("job_id") or (record.get("job_id") if record else "")
            policy = workspace.job_policy(job_key)
            if policy:
                task = replace(task, decision_mode=policy["decision_mode"],
                               min_core_percent=policy["min_core_percent"],
                               min_bonus_count=policy["min_bonus_count"])
            task = load_preview_task(task.to_dict())
            plan = plan_recruitment_task(task, preview_only=True)
            result = {"task": task.to_dict(), "plan": plan, "parsing_metadata": parsed.metadata}
            if record:
                draft = workspace.snapshots.draft(owner, tab, generation, record, text, task, parsed.metadata)
                result.update(snapshot=public_snapshot(draft))
                if not anchors_complete(task, parsed.metadata):
                    plan["blockers"].append("source_evidence_incomplete")
                    plan["status"] = "needs_clarification"
            elif not execution_matches(parsed):
                plan["blockers"].append("preview_requires_matching_rules")
                plan["status"] = "needs_clarification"
            self.reply(result)
        except (TypeError, ValueError, KeyError, AttributeError) as error:
            self.reply({"error": str(error)}, 400)

    def change_plan(self, workspace, match, payload):
        owner = self.owner()
        if match is None:
            workspace.snapshots.invalidate(owner, *self.tab_revision(payload))
            return {"invalidated": True}
        key, action = match.groups()
        if action == "revoke":
            workspace.snapshots.revoke(owner, key)
            return {"revoked": True}
        if action == "confirm":
            row = workspace.snapshots.confirm(owner, key, payload["task_hash"])
        elif action == "resize":
            row = workspace.snapshots.resize(owner, key, payload["task_hash"], payload["max_checks"])
        else:
            row = workspace.snapshots.revise(owner, key, payload["task_hash"], payload["hard_filters"],
                                             payload["soft_preferences"], payload["reason"])
        return self.plan_response(row)

    @staticmethod
    def plan_response(row):
        task = load_preview_task(row["task"])
        plan = plan_recruitment_task(task, preview_only=True)
        if row["metadata"].get("mode") != "human_override" and not anchors_complete(task, row["metadata"]):
            plan["blockers"].append("source_evidence_incomplete")
            plan["status"] = "needs_clarification"
        return {"snapshot": public_snapshot(row), "task": row["task"],
                "parsing_metadata": row["metadata"], "plan": plan}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--model-only", action="store_true", help="require a verified model API result for task parsing")
    parser.add_argument("--debug-port", type=int, default=9444)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--mock-dir", type=Path)
    parser.add_argument("--demo", action="store_true", help="use the bundled offline recruiting demo")
    parser.add_argument("--review-study", action="store_true", help="enable the bundled offline synthetic annotation study")
    parser.add_argument("--shadow-preview", action="store_true", help="include shadow results in simulated previews")
    args = parser.parse_args()
    if args.model_only and (args.demo or args.mock_dir is not None):
        parser.error("--model-only requires a live workbench")
    if args.demo:
        if args.mock_dir is not None:
            parser.error("--demo already selects its own mock data")
        args.mock_dir = HERE / "demo-fixtures"
    if args.data_dir is None:
        args.data_dir = HERE / (".demo-runtime" if args.demo else ".runtime")
    if args.demo and args.data_dir.resolve() == (HERE / ".runtime").resolve():
        parser.error("--demo requires a separate data directory")
    if (args.review_study or args.shadow_preview) and args.mock_dir is None:
        parser.error("--review-study and --shadow-preview require --mock-dir")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.model_only = args.model_only
    # 始终以本地 .env 为准：系统环境里可能有失效的旧密钥
    load_local_llm_env(ROOT / "boss-agent" / ".env", override=True)
    if args.model_only:
        os.environ["BOSS_TASK_AI_ENABLED"] = "1"
        os.environ.setdefault("BOSS_TASK_AI_TIMEOUT_SECONDS", "30")
    os.environ["NO_PROXY"] = ",".join(filter(None, [os.environ.get("NO_PROXY"), "127.0.0.1", "localhost"]))
    server.workbench = Workbench(args.data_dir, ROOT / "boss-agent",
                                 f"http://127.0.0.1:{args.debug_port}", args.mock_dir,
                                 shadow_preview=args.shadow_preview or args.demo, demo=args.demo, model_only=args.model_only)
    if args.review_study:
        server.review_study = BlindStudy(server.workbench.reviews,
                                         ROOT / "boss-agent/tests/fixtures/phase5_shadow_cases.json",
                                         ROOT / "phase5-evidence/synthetic-assessment.json",
                                         os.environ.get("BOSS_REVIEW_ADJUDICATION_KEY"))
    print(f"Local: http://127.0.0.1:{server.server_port}", flush=True)
    server.serve_forever()
