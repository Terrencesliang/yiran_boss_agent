"""Exercise the product flow with Chromium and the real local demo APIs."""

from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from types import SimpleNamespace


HERE = Path(__file__).resolve().parent
spec = spec_from_file_location("review_browser_harness", HERE / "review-browser.test.py")
harness = module_from_spec(spec)
spec.loader.exec_module(harness)

import boss_agent.task_snapshot as snapshot_module
from integration import DEMO_REQUEST


class DemoHandler(harness.Handler):
    def do_POST(self):
        if self.path == "/api/plan" and self.server.fail_next_parse:
            self.server.fail_next_parse = False
            self.reply({"error": "模拟接口暂不可用，请重试。"}, 503)
        else:
            super().do_POST()


class PausedDemo(harness.Workbench):
    proceed = Event()

    def operation(self, identifier, action, payload, progress=None):
        if action == "preview" and not self.proceed.wait(20):
            raise RuntimeError("测试未释放模拟 Worker")
        return super().operation(identifier, action, payload, progress=progress)


def fill(browser, selector, value):
    browser.js("(()=>{const field=document.querySelector(" + json.dumps(selector) + ");"
               "field.value=" + json.dumps(value) + ";field.dispatchEvent(new Event('input',{bubbles:true}));})()")


def submit(browser, selector):
    browser.js("document.querySelector(" + json.dumps(selector) + ").requestSubmit()")


def screenshot_section(browser, selector, name):
    browser.js("document.querySelector(" + json.dumps(selector) + ").scrollIntoView({block:'start'})")
    browser.screenshot(name)


def review_form(browser, reason):
    browser.js("(()=>{const form=document.querySelector('#candidate-review-form');"
               "form.elements.status.value='NEED_MORE_EVIDENCE';form.elements.reason.value=" + json.dumps(reason) +
               ";form.requestSubmit();})()")


def exercise(browser, url, desk):
    browser.command("Page.navigate", url=url)
    browser.until("document.querySelector('#mode-label')?.innerText.includes('模拟数据')")
    browser.until("document.querySelector('#parse-button')?.disabled && document.querySelector('#job').options.length===1")
    browser.click("[data-sync-task]")
    browser.until("document.querySelector('#job').options.length===3")
    browser.choose("#job", browser.js("document.querySelector('#job').options[1].value"))
    fill(browser, "#request", "本科及以上，背景合适")
    browser.until("document.querySelector('#plan-result')?.innerText.includes('需要补充信息')")
    assert not browser.js("Boolean(document.querySelector('[data-confirm-plan]'))")
    fill(browser, "#request", "本科及以上，有agent经验")
    browser.until("document.querySelector('[data-replacement=\"有 AI Agent 实操经验优先\"]')!==null")
    browser.click('[data-replacement="有 AI Agent 实操经验优先"]')
    assert browser.js("document.querySelector('#request').value") == "本科及以上，有 AI Agent 实操经验优先"
    browser.until("state.plan?.task?.soft_preferences.some(item=>item.field==='hands_on_tools')")
    browser.click(".task-example summary")
    browser.click("[data-use-example]")
    browser.until("document.querySelector('#plan-result')?.innerText.includes('模拟接口暂不可用')")
    assert browser.js("document.querySelector('#request').value") == DEMO_REQUEST
    browser.command("Network.emulateNetworkConditions",offline=True,latency=0,downloadThroughput=0,uploadThroughput=0)
    browser.click("#parse-button")
    browser.until("document.querySelector('#plan-result')?.innerText.includes('无法连接工作台')")
    browser.command("Network.emulateNetworkConditions",offline=False,latency=0,downloadThroughput=-1,uploadThroughput=-1)
    browser.click("#parse-button")
    browser.until("document.querySelector('#plan-result')?.innerText.includes('本地规则')")
    original = browser.js("state.plan.snapshot.plan_id")
    browser.js("window.scrollTo(0,0)")
    browser.screenshot("01-recruitment-task.png")
    browser.click("[data-edit-plan]")
    fill(browser, '[data-property="lower"]', "6")
    fill(browser, '[data-property="upper"]', "3")
    fill(browser, '[name="reason"]', "模拟岗位调整经验范围")
    submit(browser, "#revise-plan")
    browser.until("document.querySelector('#plan-result')?.innerText.includes('最少月数不能大于最多月数')")
    assert browser.js("state.plan.snapshot.plan_id") == original
    fill(browser, '[data-property="lower"]', "0")
    fill(browser, '[data-property="upper"]', "10")
    submit(browser, "#revise-plan")
    browser.until("state.plan?.snapshot?.snapshot_revision===2 && !state.editPlan")
    assert browser.js("state.plan.snapshot.plan_id") != original
    assert browser.js("Boolean(document.querySelector('[data-run-plan]')) && !document.querySelector('[data-confirm-plan]')")
    screenshot_section(browser, "#plan-result", "02-confirm-conditions.png")
    browser.click("[data-run-plan]")
    browser.until("document.querySelector('#confirm-dialog').open")
    browser.click('#confirm-dialog [value="cancel"]')
    browser.until("!document.querySelector('#confirm-dialog').open && !state.previewSubmitting")
    assert browser.js("state.plan?.snapshot?.status==='confirmed'")
    assert browser.js("workspace.runs.filter(item=>item.action==='preview').length") == 0
    snapshot = browser.js("state.plan.snapshot.plan_id")
    task_hash = browser.js("state.plan.snapshot.task_hash")
    browser.command("Page.reload")
    browser.until("Boolean(document.querySelector('[data-run-plan]'))")
    assert browser.js("state.plan.snapshot.plan_id") == snapshot
    browser.click("[data-run-plan]")
    browser.click('#confirm-dialog [value="cancel"]')
    browser.until("!document.querySelector('#confirm-dialog').open && !state.previewSubmitting")
    assert browser.js("workspace.runs.filter(item=>item.action==='preview').length") == 0
    browser.click("[data-run-plan]")
    browser.click("#confirm-submit")
    browser.until("document.querySelector('#task-run')?.innerText.includes('正在执行')")
    assert not browser.js("document.querySelector('#task-run').innerText.includes('%')")
    assert browser.js("workspace.runs.filter(item=>item.action==='preview').length") == 1
    screenshot_section(browser, "#task-run", "03-preview-running.png")
    desk.proceed.set()
    browser.click("#task-run [data-review-run]")
    browser.until("document.querySelectorAll('.review-table tbody tr').length===30")
    assert browser.js("reviewState.data.summary") == {"FAIL":9,"UNKNOWN":17,"CAPTURE_FAILED":4}
    assert browser.js("reviewState.data.snapshot.task_hash") == task_hash
    run = browser.js("reviewState.run")
    browser.screenshot("04-candidate-results.png")
    fill(browser, "#review-search", "不存在的模拟候选人")
    browser.until("document.querySelector('#review-table-results')?.innerText.includes('没有符合筛选条件')")
    fill(browser, "#review-search", "模拟人01")
    browser.until("document.querySelectorAll('.review-table tbody tr').length===1")
    assert browser.js("document.querySelector('.review-table tbody tr').innerText.includes('本科')")
    assert browser.js("document.querySelector('[data-review-candidate=\"mock-01\"]').textContent") == "模拟人01"
    assert browser.js("reviewState.data.rows.every(row=>!('resume_sources' in row))")
    assert browser.js("workspace.runs.every(run=>run.result?.review_candidates?.every(row=>!('candidate_profile' in row)) ?? true)")
    browser.click('[data-review-candidate="mock-01"]')
    browser.until("document.querySelector('#review-detail').open")
    assert browser.js("document.querySelector('#review-title').textContent.startsWith('模拟人01')")
    assert browser.js("document.querySelector('#review-body details[open]').innerText.includes('工具：Excel')")
    browser.command("Emulation.setDeviceMetricsOverride", width=1440, height=900, deviceScaleFactor=1, mobile=False)
    browser.until("innerWidth===1440")
    assert browser.js("""getComputedStyle(document.querySelector('.candidate-layout')).gridTemplateColumns.split(' ').length===2""")
    browser.screenshot("05-candidate-detail.png")
    browser.command("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=1, mobile=True)
    browser.until("innerWidth===390")
    browser.screenshot("05-candidate-detail-mobile.png")
    mobile_layout=browser.js("""(()=>{const dialog=document.querySelector('#review-detail');
        return [dialog.offsetWidth,dialog.scrollWidth-dialog.clientWidth,
            getComputedStyle(document.querySelector('.candidate-layout')).gridTemplateColumns.split(' ').length];
    })()""")
    assert mobile_layout==[390,0,1], mobile_layout
    browser.command("Emulation.clearDeviceMetricsOverride")
    assert browser.js("document.querySelector('#review-body').innerText.includes('未取得专业证据') && "
                      "document.querySelector('#review-body').innerText.includes('未取得工作经历正文')")
    source = browser.socket
    stale_id, stale = browser.new_tab(url+"#review")
    browser.socket = stale
    browser.until("document.querySelectorAll('.review-table tbody tr').length===30")
    browser.click('[data-review-candidate="mock-01"]')
    browser.until("document.querySelector('#review-detail').open")
    browser.socket = source
    reason = "模拟招聘复核：缺少专业原文"
    review_form(browser, reason)
    browser.until("document.querySelector('#review-feedback')?.textContent==='已保存人工复核'")
    browser.socket = stale
    review_form(browser, "旧标签页提交")
    browser.until("document.querySelector('#review-feedback')?.textContent.includes('有更新的复核版本')")
    browser.socket = source
    browser.command("Target.closeTarget", targetId=stale_id)
    stale.close(timeout=0.2)
    browser.command("Page.bringToFront")
    browser.command("Page.reload")
    browser.until("document.querySelectorAll('.review-table tbody tr').length===30")
    browser.click('[data-review-candidate="mock-01"]')
    browser.until("document.querySelector('#review-body')?.innerText.includes("+json.dumps(reason)+")")
    assert browser.js("document.querySelector('[data-official-status]').dataset.officialStatus") == "UNKNOWN"
    browser.js("[...document.querySelectorAll('#review-body h3')].find(el=>el.textContent==='人工复核').scrollIntoView({block:'start'})")
    browser.screenshot("06-manual-review.png")
    browser.click("[data-review-close]")
    harness.rich_run(desk)
    browser.js("refreshWorkspace(true)")
    browser.choose("#review-run", harness.RICH)
    browser.click('[data-review-candidate="major-citation-conflict"]')
    browser.until("document.querySelector('#review-detail[open]')?.dataset.candidateId==='major-citation-conflict'")
    assert browser.js("document.querySelector('#review-body').innerText.includes('软件工程') && document.querySelector('#review-body').innerText.includes('通信工程')")
    browser.click("[data-review-close]")
    browser.click('[data-review-candidate="hr-leap-exact"]')
    browser.until("document.querySelector('#review-detail').open && document.querySelector('#review-body').innerText.includes('366 天')")
    browser.click("[data-review-close]")
    browser.click('[data-page="runs"]')
    fill(browser, "#run-query", "筛选预览")
    browser.until("document.querySelectorAll('#run-list tbody tr').length===2")
    assert browser.js("document.querySelector('#run-list').innerText.includes('30')")
    browser.click('[data-page="settings"]')
    browser.click("[data-browser-check]")
    browser.until("document.body.innerText.includes('本地服务已响应')")
    browser.command("Emulation.setDeviceMetricsOverride",width=390,height=850,deviceScaleFactor=1,mobile=True)
    browser.click('[data-page="tasks"]')
    assert browser.js("document.documentElement.scrollWidth===window.innerWidth")
    browser.screenshot("07-narrow-task.png")
    browser.command("Emulation.clearDeviceMetricsOverride")
    original_clock = snapshot_module.time
    try:
        snapshot_module.time = SimpleNamespace(time=lambda: original_clock.time()+7200)
        browser.command("Page.reload")
        browser.until("document.querySelector('#plan-result')?.innerText.includes('已过期')")
        assert browser.js("document.querySelector('#request').value") == DEMO_REQUEST
        assert not browser.js("Boolean(document.querySelector('[data-run-plan]'))")
    finally:
        snapshot_module.time = original_clock
    browser.click("[data-repeat-preview]")
    browser.until("Boolean(document.querySelector('[data-run-plan]'))")
    browser.js("document.querySelector('[data-boss-page-filter]').closest('details').open=true")
    browser.choose('[data-boss-page-filter="活跃度"]', "今日活跃")
    browser.choose('[data-boss-page-filter="性别"]', "女")
    browser.js("""(()=>{ const age=document.querySelector('[data-boss-page-filter="年龄"]');
        age.value='25-35'; age.dispatchEvent(new Event('change',{bubbles:true})); })()""")
    browser.js("""(()=>{for (const name of ['电子信息类','计算机类']) {
        document.querySelector('[data-boss-major][value="'+name+'"]').click();
    }})()""")
    browser.until("state.plan?.task?.boss_page_filters?.['专业']==='电子信息类,计算机类'")
    assert browser.js("state.previewRun==='' && document.querySelector('#request').value") == DEMO_REQUEST
    fill(browser, '#preview-count-input', "12")
    browser.click("[data-run-plan]")
    browser.until("document.querySelector('#confirm-dialog').open")
    browser.click("#confirm-submit")
    browser.until("state.plan?.task?.max_checks===12")
    browser.until(f"Boolean(state.previewRun) && state.previewRun!=='{run}'")
    browser.until("document.querySelector('#task-run [data-review-run]')?.dataset.reviewRun===state.previewRun")
    assert browser.js("state.plan.task.boss_page_filters['活跃度']") == "今日活跃"
    repeat_id = browser.js("state.previewRun")
    repeat_report = json.loads((desk.directory / f"{repeat_id}-preview.json").read_text(encoding="utf-8"))
    assert repeat_report["max_checks"] == repeat_report["summary"]["checked"] == 12
    assert repeat_report["page_filters"]["活跃度"] == "今日活跃"
    assert repeat_report["page_filters"]["年龄"] == "25-35"
    assert repeat_report["page_filters"]["性别"] == "女"
    assert repeat_report["page_filters"]["专业"] == "电子信息类,计算机类"
    assert not browser.runtime_errors, browser.runtime_errors
    print(json.dumps({"status":"passed","run_id":run,"task_hash":task_hash,"checked":30,
                      "official":{"PASS":0,"FAIL":9,"UNKNOWN":17,"CAPTURE_FAILED":4},
                      "browser":"Chromium", "artifacts":7},ensure_ascii=False))


def main():
    binaries = [os.environ.get("PHASE6_CHROMIUM"), r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"]
    binary = next((Path(item) for item in binaries if item and Path(item).exists()),None)
    if binary is None:
        raise SystemExit("Set PHASE6_CHROMIUM to a local Chromium executable")
    with TemporaryDirectory(prefix="workbench-product-browser-") as temp:
        root=Path(temp)
        desk=PausedDemo(root/"data",harness.ROOT,"http://example.invalid",HERE/"demo-fixtures",demo=True,shadow_preview=True)
        server=harness.ThreadingHTTPServer(("127.0.0.1",0),DemoHandler)
        server.workbench=desk
        server.fail_next_parse=False
        thread=Thread(target=server.serve_forever)
        thread.start()
        url=f"http://127.0.0.1:{server.server_port}/"
        browser=None
        try:
            # Fail only the sample's first parse after the genuine blocker case.
            original=DemoHandler.do_POST
            def intercept(handler):
                if handler.path=="/api/plan" and not getattr(server,"saw_blocker",False):
                    server.saw_blocker=True
                    original(handler)
                    server.fail_next_parse=True
                else:
                    original(handler)
            DemoHandler.do_POST=intercept
            browser=harness.Chromium(binary,root/"profile",url)
            exercise(browser,url,desk)
        finally:
            desk.proceed.set()
            if browser:browser.close()
            server.shutdown()
            thread.join()
            server.server_close()
            desk.executor.shutdown(wait=True)


if __name__ == "__main__":
    main()
