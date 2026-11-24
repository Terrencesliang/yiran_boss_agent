"""Real Chromium UI smoke test against a local synthetic-only workbench."""

from datetime import date
from base64 import b64decode
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import time
from urllib.request import ProxyHandler, build_opener

from websocket import create_connection

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT / "boss-agent/src"), str(ROOT / "boss-agent/tests")]

from boss_agent.candidate_adapter import adapt_recommend_candidate
from boss_agent.candidate_evidence import evidence_report
from boss_agent.job_profile import load_condition
from boss_agent.task_schema import RecruitmentTask
from boss_agent.task_snapshot import digest
from boss_agent.shadow_rule_engine import evaluate_shadow
from boss_agent.recommendation import rank_recruitment_preview
from reviews import BlindStudy
from server import Handler, ThreadingHTTPServer, Workbench
from test_browser_recruitment_preview import detail
from test_shadow_rule_engine import CASES, condition
from test_workbench_reviews import HASH, OWNER, RUN, TOKEN, seed_run


RICH = "aabbccddeeff"
RICH_CASES = ["major-standard", "major-outside", "major-missing", "major-citation-conflict", "hr-leap-exact",
              "hr-over", "hr-only-start", "hr-overlap", "fail-and-unknown", "identity-conflict", "major-undefined-scope"]


def rich_run(workspace):
    report = json.loads((ROOT / "phase5-evidence/confirmed-preview-30.json").read_text(encoding="utf-8"))
    scenario_cases = [next(item for item in CASES if item["id"] == key) for key in RICH_CASES]
    conflict = next(item for item in CASES if item["id"] == "major-citation-conflict")
    scenario_cases.append({**conflict, "id": "or-pass-with-conflict",
                           "hard": [{"operator": "OR", "conditions": conflict["hard"]}]})
    shadow_rows = []
    buckets = {"PASS":"ranked_candidates", "FAIL":"rejected_candidates", "UNKNOWN":"review_candidates",
               "CAPTURE_FAILED":"capture_failed_candidates"}
    for bucket in buckets.values():
        report[bucket] = []
    for case in scenario_cases:
        identifier = case["id"]
        task = RecruitmentTask("合成 QA 场景集", [load_condition(condition(rule)) for rule in case["hard"]],
                               [], None, None, "screen")
        if case.get("invalidateIdentity"):
            source = {"candidate_id":identifier, "capture_status":"failed"}
            evidence = {"identity_status":"UNVERIFIED","capture_status":"failed",
                        "issues":["detail_identity_mismatch_or_unverified"],"evidence":{}}
        else:
            ocr = {"ocrText":case["ocr"],"ocrStatus":"ok"} if case.get("ocr") else {}
            candidate = adapt_recommend_candidate({"candidateId":identifier}, {"opened":True,"candidateId":identifier},
                                                    detail(identifier,case["dom"],**ocr))
            source = candidate.to_ranking_input()
            evidence = evidence_report(source,"phase6-browser",date(2026,1,1),task=task)
        ranked = rank_recruitment_preview(task,[source],as_of=date(2026,1,1))
        for name in buckets.values():
            for row in ranked[name]:
                row["candidate_evidence"] = evidence
                report[name].append(row)
        shadow_rows.append({"candidate_id":identifier,"shadow":evaluate_shadow(task,evidence,source,[])})
    report["shadow_evaluation"]["rows"] = shadow_rows
    # This report is an isolated static visualization gallery. Each row was
    # evaluated against its own synthetic scenario, never against this header.
    report["job"] = "合成 QA 场景集"
    report["task"] = {**report["task"],"job":report["job"],"hard_filters":[],"soft_preferences":[]}
    audit = {**report["snapshot_execution"],"run_id":RICH,"task_hash":digest(report["task"])}
    report["snapshot_execution"] = audit
    report["summary"]["checked"] = len(scenario_cases)
    (workspace.directory / f"{RICH}-preview.json").write_text(json.dumps(report),encoding="utf-8")
    workspace.state["runs"].insert(0,{**audit,"id":RICH,"action":"preview","status":"completed",
                                     "job":report["job"],"_snapshot_execution":{"owner":OWNER}})
    workspace.persist()


class Chromium:
    def __init__(self, binary, profile, url):
        self.opener = build_opener(ProxyHandler({}))
        self.proc = subprocess.Popen([
            str(binary), "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-background-networking", "--no-first-run",
            "--disable-component-update", "--disable-extensions", "--no-default-browser-check", "--no-proxy-server",
            "--disable-sync", "--disable-default-apps", "--password-store=basic", "--remote-debugging-port=0",
            "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1", "--window-size=1400,940",
            f"--user-data-dir={profile}", "about:blank",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        active = profile / "DevToolsActivePort"
        try:
            self.wait(lambda:active.exists() and bool(active.read_text().strip()))
            port = active.read_text().splitlines()[0]
            self.targets_url=f"http://127.0.0.1:{port}/json/list"
            with self.opener.open(self.targets_url, timeout=5) as stream:
                target = next(item for item in json.load(stream) if item["type"]=="page")
            self.socket = create_connection(target["webSocketDebuggerUrl"], timeout=8, suppress_origin=True,
                                            http_no_proxy=["127.0.0.1","localhost"])
            self.seq = 0
            self.runtime_errors = []
            self.command("Runtime.enable")
            self.command("Network.enable")
            self.command("Network.setCookie",name="workbench_owner",value=TOKEN,url=url,httpOnly=True,sameSite="Strict")
            self.command("Page.enable")
            self.navigate(url+"#review")
        except BaseException:
            self.close()
            raise

    @staticmethod
    def wait(check):
        deadline = time.monotonic()+12
        while time.monotonic()<deadline:
            if check():
                return
            time.sleep(.05)
        raise AssertionError("browser operation timed out")

    def command(self, method, **params):
        self.seq += 1
        current=self.seq
        self.socket.send(json.dumps({"id":current,"method":method,"params":params}))
        while True:
            message=json.loads(self.socket.recv())
            if message.get("method")=="Runtime.exceptionThrown":
                self.runtime_errors.append(message["params"]["exceptionDetails"])
            if message.get("id")==current:
                if "error" in message:
                    raise AssertionError(message["error"])
                return message["result"]

    def js(self, expression):
        value=self.command("Runtime.evaluate",expression=expression,returnByValue=True,awaitPromise=True)
        if "exceptionDetails" in value:
            raise AssertionError(value["exceptionDetails"])
        return value["result"].get("value")

    def navigate(self, url):
        self.command("Page.navigate",url=url)

    def new_tab(self,url):
        target=self.command("Target.createTarget",url=url)["targetId"]
        with self.opener.open(self.targets_url,timeout=5) as stream:
            tab=next(item for item in json.load(stream) if item["id"]==target)
        return target, create_connection(tab["webSocketDebuggerUrl"],timeout=8,suppress_origin=True,
                                         http_no_proxy=["127.0.0.1","localhost"])

    def until(self, expression):
        try:
            self.wait(lambda:self.js(expression))
        except AssertionError as error:
            raise AssertionError(expression+"\n"+str(self.js("document.body.innerText.slice(0,2800)"))) from error

    def click(self, selector):
        self.until(f"Boolean(document.querySelector({json.dumps(selector)}))")
        self.js(f"document.querySelector({json.dumps(selector)}).click()")

    def choose(self, selector, value):
        self.until(f"Boolean(document.querySelector({json.dumps(selector)}))")
        self.js(f"(()=>{{const el=document.querySelector({json.dumps(selector)}); el.value={json.dumps(value)}; "
                "el.dispatchEvent(new Event('change',{bubbles:true}));})()")

    def close(self):
        if hasattr(self,"socket"):
            self.socket.close()
        if self.proc.poll() is None:
            self.proc.terminate()
        self.proc.wait(timeout=8)

    def screenshot(self,name):
        destination=os.environ.get("PHASE6_EVIDENCE_DIR")
        if destination:
            path=Path(destination)
            path.mkdir(parents=True,exist_ok=True)
            (path/name).write_bytes(b64decode(self.command("Page.captureScreenshot",format="png")["data"]))


def exercise(browser, url):
    browser.until("document.querySelectorAll('.review-table tbody tr').length===30")
    assert browser.js("""(async()=>{
        const original=api;
        api=async (url,options)=>{
            const result=await original(url,options);
            if(url.endsWith('/candidates')) for(const row of result.rows) delete row.education_summary;
            return result;
        };
        try {
            await openReviewRun(reviewState.run);
            return document.querySelector('[data-review-education="mock-01"]').innerText.includes('本科');
        } finally { api=original; }
    })()""")
    browser.click(".review-advanced summary")
    assert browser.js(f"document.body.innerText.includes('{HASH}')")
    browser.screenshot("candidates-30.png")
    browser.choose('[data-review-filter="official"]',"UNKNOWN")
    browser.until("document.querySelectorAll('.review-table tbody tr').length===17")
    assert browser.js("[...document.querySelectorAll('.review-table tbody tr')].every(row=>row.innerText.includes('未取得专业证据'))")
    browser.click('[data-review-candidate="mock-01"]')
    browser.until("document.querySelector('#review-detail').open")
    assert browser.js("document.querySelector('#review-body').innerText.includes('未取得工作经历正文')")
    assert browser.js("!document.querySelector('#candidate-section-technical').open")
    assert browser.js("document.querySelector('.candidate-facts').innerText.includes('本科')")
    browser.click('[data-candidate-section="resume"]')
    assert browser.js("document.querySelector('#candidate-section-evidence').open")
    browser.click('[data-proof-mode="offset"]')
    assert browser.js("!document.querySelector('[data-proof-pane=offset]').hidden")
    assert browser.js("document.querySelector('[data-proof-pane=source]').hidden")
    browser.js("document.querySelector('.candidate-facts + details, .candidate-facts ~ details').open=true")
    browser.click('#review-body [data-review-ref]')
    assert browser.js("document.activeElement.id.startsWith('review-ref-')")
    assert browser.js("!document.querySelector('[data-proof-pane=source]').hidden")
    source_tab=browser.socket
    stale_id,stale_tab=browser.new_tab(url+"#review")
    browser.socket=stale_tab
    browser.until("document.querySelectorAll('.review-table tbody tr').length===30")
    browser.click('[data-review-candidate="mock-01"]')
    browser.until("document.querySelector('#review-detail').open")
    browser.socket=source_tab
    review_form="""(()=>{const form=document.querySelector('#candidate-review-form');
        form.elements.status.value='NEED_MORE_EVIDENCE'; form.elements.reason.value='浏览器模拟：缺少专业原文';
        form.requestSubmit();})()"""
    browser.js(review_form)
    browser.until("document.querySelector('#review-feedback')?.textContent==='已保存人工复核'")
    browser.socket=stale_tab
    browser.js(review_form)
    browser.until("document.querySelector('#review-feedback')?.textContent.includes('有更新的复核版本')")
    browser.socket=source_tab
    browser.command("Target.closeTarget",targetId=stale_id)
    stale_tab.close(timeout=0.2)
    browser.command("Page.bringToFront")
    if output:=os.environ.get("PHASE6_EVIDENCE_DIR"):
        record=browser.js(f"fetch('/api/runs/{RUN}/reviews').then(response=>response.json())")
        (Path(output)/"synthetic-review-audit.json").write_text(json.dumps(record,ensure_ascii=False,indent=2)+"\n",
                                                               encoding="utf-8")
    browser.navigate(url+"#review")
    browser.command("Page.reload")
    browser.until("document.querySelectorAll('.review-table tbody tr').length===30")
    browser.click('[data-review-candidate="mock-01"]')
    browser.until("document.querySelector('#review-body')?.innerText.includes('浏览器模拟：缺少专业原文')")
    assert browser.js("document.querySelector('.candidate-heading .pill').textContent.trim()==='不符合'")
    browser.click("[data-review-close]")
    browser.choose('[data-review-filter="official"]',"CAPTURE_FAILED")
    browser.until("document.querySelectorAll('.review-table tbody tr').length===4")
    browser.choose('[data-review-filter="official"]',"")
    browser.choose("#review-run",RICH)
    browser.until("document.querySelectorAll('.review-table tbody tr').length===12")
    browser.click('[data-review-candidate="major-citation-conflict"]')
    browser.until("document.querySelector('#review-detail[open]')?.dataset.candidateId==='major-citation-conflict'")
    assert browser.js("document.querySelector('[data-shadow-status]').dataset.shadowStatus==='CONFLICT'")
    assert browser.js("document.querySelector('#review-body').innerText.includes('软件工程') && document.querySelector('#review-body').innerText.includes('通信工程')")
    for source in ("DOM","OCR"):
        selector=f'[data-review-ref][data-source="{source}"]'
        clicked=browser.js(f"""(()=>{{const link=document.querySelector('#review-body {selector}');
          if(link)link.click(); return Boolean(link) && document.activeElement.id.startsWith('review-ref-');}})()""")
        assert clicked
    browser.js("""(()=>{document.querySelector('#review-body .review-citations').scrollIntoView({block:'start'});
        document.querySelector('#review-detail').scrollTop-=95;})()""")
    browser.screenshot("major-conflict.png")
    browser.click("[data-review-close]")
    browser.click('[data-review-candidate="hr-leap-exact"]')
    browser.until("document.querySelector('#review-body')?.innerText.includes('366 天')")
    browser.js("[...document.querySelectorAll('#review-body h3')].find(e=>e.textContent==='工作经历').scrollIntoView({block:'center'})")
    browser.screenshot("hr-timeline.png")
    browser.click("[data-review-close]")
    for candidate in ("major-standard","major-outside","major-missing","hr-over","hr-only-start","hr-overlap",
                      "fail-and-unknown","or-pass-with-conflict","identity-conflict","major-undefined-scope"):
        browser.click(f'[data-review-candidate="{candidate}"]')
        browser.until(f"document.querySelector('#review-detail[open]')?.dataset.candidateId==='{candidate}'")
        expected=next((item["status"] for item in CASES if item["id"]==candidate),"PASS")
        assert browser.js("document.querySelector('[data-shadow-status]').dataset.shadowStatus")==expected
        if candidate=="hr-only-start":
            assert browser.js("document.querySelector('#review-body').innerText.includes('总时长无法确认')")
        if candidate=="hr-overlap":
            assert browser.js("JSON.parse(document.querySelector('#candidate-section-technical > details:last-child > pre').textContent).人力资源工作时长.overlap_detected")
        if candidate=="or-pass-with-conflict":
            browser.js("for(const group of document.querySelectorAll('#review-body details.evidence'))group.open=true")
            assert browser.js("Boolean(document.querySelector('#review-body details.evidence .pill.CONFLICT'))")
        browser.click("[data-review-close]")
    browser.click('[data-page="differences"]')
    browser.until("""Boolean(document.querySelector('[data-review-candidate="major-undefined-scope"]'))""")
    browser.click("[data-study-open]")
    browser.until("document.querySelector('#study-case')?.options.length===45")
    first=browser.js("document.querySelector('#study-case').options[1].value")
    browser.choose("#study-case",first)
    browser.until("Boolean(document.querySelector('#study-annotation'))")
    assert browser.js("!document.querySelector('.review-study-body').innerText.includes('旧资格')")
    browser.screenshot("blind-annotation.png")
    browser.js("""(()=>{const form=document.querySelector('#study-annotation');
        for(const row of form.querySelectorAll('[data-study-row]')){
            row.querySelector('[name=status]').value='UNKNOWN';
            row.querySelector('[name=reason]').value='浏览器独立模拟标注';
        }
        form.requestSubmit();})()""")
    browser.until("document.querySelector('.review-study-body')?.innerText.includes('旧资格')")
    assert browser.js("document.querySelector('.review-study-body').innerText.includes('浏览器独立模拟标注')")
    print("Chromium: 30 rows; 17 missing-evidence and 4 failed filters; 12 scenarios; 3/3 citation targets; two-tab conflict; review persistence; blind reveal")


def main():
    candidates = [os.environ.get("PHASE6_CHROMIUM"), r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                  r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"]
    binary = next((Path(item) for item in candidates if item and Path(item).exists()),None)
    if binary is None:
        raise SystemExit("Set PHASE6_CHROMIUM to a local Chromium executable")
    with TemporaryDirectory(prefix="boss-review-browser-") as temporary:
        folder=Path(temporary)
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        server.workbench=Workbench(folder/"data",ROOT/"boss-agent","http://example.invalid",folder)
        seed_run(server.workbench)
        rich_run(server.workbench)
        server.review_study=BlindStudy(server.workbench.reviews,ROOT/"boss-agent/tests/fixtures/phase5_shadow_cases.json",
                                      ROOT/"phase5-evidence/synthetic-assessment.json")
        thread=Thread(target=server.serve_forever)
        thread.start()
        url=f"http://127.0.0.1:{server.server_port}/"
        try:
            browser=Chromium(binary,folder/"profile",url)
            try:
                exercise(browser,url)
            finally:
                browser.close()
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
            server.workbench.executor.shutdown(wait=True)


if __name__ == "__main__":
    main()
