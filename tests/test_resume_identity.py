from unittest.mock import Mock
import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from boss_agent.chrome_debug import (
    ChromeDebugClient,
    build_close_recommend_detail_expression_v2,
    build_verified_recommend_detail_expression,
)


def test_ocr_is_discarded_when_verified_document_changes(monkeypatch):
    client = ChromeDebugClient("http://127.0.0.1:9222")
    monkeypatch.setattr(client, "list_pages", lambda: [{"webSocketDebuggerUrl": "mock"}])
    before = {"detailCandidateId": "real-1", "captureStatus": "ready",
              "fullDomText": "简历内容", "resumeFrameSrc": "/frame/one", "canvasExpected": True}
    read = Mock(side_effect=[before, before, {**before, "resumeFrameSrc": "/frame/two"}])
    monkeypatch.setattr(client, "_evaluate_json", read)
    monkeypatch.setattr(client, "_wait_recommend_resume_canvas_ready", lambda *args, **kw: {"ready": True})
    monkeypatch.setattr(client, "_augment_resume_with_canvas_ocr", lambda _, resume: {**resume, "ocrText": "原文"})
    result = client.capture_candidate_resume(expected_candidate_id="real-1")
    assert result == {"captureStatus": "detail_changed_during_ocr"}
    assert read.call_count == 3


@pytest.mark.skipif(not shutil.which("node"), reason="Node executes the browser identity transaction")
def test_card_open_binds_unlabelled_detail_and_rejects_changed_identity(monkeypatch):
    client = ChromeDebugClient("http://127.0.0.1:9222")
    monkeypatch.setattr(client, "list_pages", lambda: [{"webSocketDebuggerUrl": "mock"}])
    monkeypatch.setattr("boss_agent.chrome_debug.uuid4", lambda: SimpleNamespace(hex="binding-test"))
    captured = []
    monkeypatch.setattr(client, "_evaluate_json", lambda _, expression, **kw: captured.append(expression) or {})
    client.open_recommend_candidate("stable-a", strict_identity=True)
    script = """
let activeRoots = [], cardId = 'stable-a';
const listeners = {};
const rect = () => ({width:200,height:100,top:0,bottom:100,left:0,right:200});
const style = {innerHeight:800,innerWidth:1200,
  getComputedStyle:()=>({display:'block',visibility:'visible'})};
const doc = {defaultView:style,documentElement:{},body:{},
  querySelector:()=>null,
  querySelectorAll:selector=>selector === 'li.card-item' ? [card]
    : selector.includes('.resume-common-dialog') ? activeRoots : [],
  addEventListener:(type,handler)=>{listeners[type]=handler;},
  removeEventListener:(type,handler)=>{if(listeners[type]===handler) delete listeners[type];}};
const root = {ownerDocument:doc,isConnected:true,innerText:'教育经历\\n学历：全日制本科',
  getClientRects:()=>[1],contains:other=>other===root,closest:()=>null,matches:()=>false,
  getAttribute:()=>null,querySelectorAll:()=>[]};
const clickable = {click:()=>{activeRoots=[root];}};
const card = {isConnected:true,ownerDocument:doc,
  innerText:'stable-a candidate card longer than twenty characters',
  getBoundingClientRect:rect,getAttribute:key=>key==='data-geekid'?cardId:null,
  querySelector:selector=>selector.startsWith('.card-inner')?clickable:null,
  querySelectorAll:()=>[],matches:()=>true,closest:()=>null,scrollIntoView:()=>{}};
globalThis.document=doc;globalThis.window=style;
globalThis.MutationObserver=class {observe(){} disconnect(){}};
globalThis.setTimeout=callback=>callback();
"""
    read = build_verified_recommend_detail_expression("binding-test")
    wrong_token = build_verified_recommend_detail_expression("other-token")
    script += f"""
(async()=>{{
  const opened=JSON.parse(await {captured[0]});
  const read=()=>JSON.parse({read});
  const ready=read();
  const duplicateOpen=JSON.parse(await {captured[0]});
  cardId='changed';const changedCard=read();cardId='stable-a';
  const wrongToken=JSON.parse({wrong_token});
  listeners.click({{target:{{closest:()=>card}}}});
  const invalidated=read();
  doc[Symbol.for('boss-agent.recommend-detail')].invalidated=false;
  activeRoots=[{{...root}}];const replacedRoot=read();activeRoots=[root];
  root.isConnected=false;const disconnected=read();
  console.log(JSON.stringify({{opened,ready,duplicateOpen,changedCard,wrongToken,invalidated,replacedRoot,disconnected}}));
}})().catch(error=>{{console.error(error);process.exitCode=1;}});
"""
    completed = subprocess.run(["node"], input=script, encoding="utf-8", capture_output=True, check=True)
    result = json.loads(completed.stdout)
    assert result["opened"]["candidateId"] == result["ready"]["detailCandidateId"] == "stable-a"
    assert result["ready"]["captureStatus"] == "ready"
    assert result["ready"]["identitySource"] == "verified_card_open"
    assert result["duplicateOpen"]["reason"] == "prior_detail_not_closed"
    for key in ("changedCard", "wrongToken", "invalidated", "replacedRoot", "disconnected"):
        assert result[key]["captureStatus"] == "detail_identity_unverified"


@pytest.mark.skipif(not shutil.which("node"), reason="Node executes the scoped close control")
def test_close_button_uses_outer_resume_root_and_frame_coordinates():
    locate = build_close_recommend_detail_expression_v2(locate_only=True)
    close = build_close_recommend_detail_expression_v2()
    script = """
let clicks=0;
const style={getComputedStyle:()=>({display:'block',visibility:'visible'})};
const rect=()=>({width:40,height:40,left:900,top:10});
const doc={defaultView:style,documentElement:{},querySelectorAll:()=>[inner,root]};
const button={className:'close-btn',innerText:'',ownerDocument:doc,getBoundingClientRect:rect,
  click:()=>{clicks++;}};
const root={className:'lib-standard-resume',innerText:'工作经历'+ '经历'.repeat(30),
  ownerDocument:doc,getBoundingClientRect:rect,closest:()=>root,
  querySelectorAll:selector=>selector==='.close-btn'?[button]:[]};
const inner={...root,closest:selector=>selector.includes('.lib-standard-resume')?root:inner,
  querySelectorAll:()=>[]};
globalThis.document={querySelector:()=>({contentDocument:doc,getBoundingClientRect:()=>({left:100,top:90})})};
""" + f"const located=JSON.parse({locate}); const closed=JSON.parse({close});console.log(JSON.stringify({{located,closed,clicks}}));"
    completed = subprocess.run(["node"], input=script, encoding="utf-8", capture_output=True, check=True)
    result = json.loads(completed.stdout)
    assert result["located"]["method"] == "locate" and not result["located"]["closed"]
    assert result["located"]["button"]["x"] == 1020
    assert result["located"]["button"]["y"] == 120
    assert result["closed"]["closed"] and result["clicks"] == 1


def test_focus_emulation_is_scoped_to_a_releasable_debug_session(monkeypatch):
    client = ChromeDebugClient("http://127.0.0.1:9222")
    monkeypatch.setattr(client, "list_pages", lambda: [{"webSocketDebuggerUrl": "mock"}])
    socket = Mock()
    monkeypatch.setattr("boss_agent.chrome_debug.create_connection", Mock(return_value=socket))
    send = Mock(return_value={"result": {}})
    monkeypatch.setattr(client, "_send_cdp_command", send)
    client.activate_recommend_page("/recommend")
    assert [call.args[1] for call in send.call_args_list] == ["Page.bringToFront", "Emulation.setFocusEmulationEnabled"]
    socket.close.assert_not_called()
    client.release_recommend_page()
    client.release_recommend_page()
    socket.close.assert_called_once()
