const productDate=value=>value?new Date(value).toLocaleString("zh-CN"):"—";
const operationLabel=run=>({preview:"筛选预览",sync:"同步岗位"}[run.action] || actions[run.action] || run.action);

function updateWorkspaceStatus() {
  const connection=$("#connection-label");
  if(connection)connection.textContent=workspace.connection==="online"?"本地服务已连接":workspace.connection==="offline"?"本地服务未连接":"正在连接工作台";
  const mode=$("#mode-label");
  if(mode)mode.textContent=workspace.connection==="offline"?"连接中断":workspace.connection==="pending"?"正在连接":
    workspace.mode==="mock"?"离线演示 · 模拟数据":"连接模式";
  document.querySelectorAll("[data-live-only]").forEach(item=>{item.hidden=workspace.demo===true;});
  if(state.page==="tasks")updateTaskStatus();

}
function syncStatus() {
  if(workspace.connection==="offline")return "本地服务未连接，恢复后可重新同步。";
  const sync=workspace.runs.find(run=>run.action==="sync");
  if(state.syncSubmitting || ["queued","running"].includes(sync?.status))return "正在同步岗位…";
  if(sync?.status==="failed")return "最近一次同步失败："+(sync.error || "请重试。");
  if(sync?.status==="interrupted")return "最近一次同步已中断，请重新同步。";
  if(sync?.status==="completed"){
    const available=workspace.job_records.length;
    const found=sync.result?.found;
    if(Number.isInteger(found) && found>available){
      return "读取到 "+found+" 个岗位，其中 "+available+" 个可选。其余岗位的标识未通过核验，请在 BOSS「推荐牛人」检查后重新同步。";
    }
    return "已同步 "+available+" 个可用岗位 · "+productDate(sync.finished || sync.created);
  }
  return "暂无岗位同步记录。";
}
const greetReasonLabels={
  confirmed:"已打招呼",already_recorded:"此前已联系",candidate_not_found:"当前 BOSS 推荐列表未找到原候选人",
  current_evidence_not_qualified:"当前证据不再符合",send_unconfirmed:"发送状态未确认",
  prior_send_unconfirmed:"上次发送未确认",page_challenge_detected:"页面出现验证，已停止",
  send_state_unverified:"发送记录无法核对，已停止",
  job_context_changed:"岗位上下文变化，已停止",detail_cleanup_unverified:"详情页未确认关闭，已停止",
};
const greetErrorLabels={
  criteria_required:"筛选条件不完整，无法核验候选人资格，本次未发送。",
  live_contact_requires_browser_preview:"这份预览不是来自已连接的 BOSS 页面，出于安全没有发送。请在 BOSS 浏览器里重新预览后再联系。",
  shortlist_not_qualified_in_preview:"所选候选人在最新核验中已不再符合要求，请重新筛一轮。",
  explicit_contact_shortlist_required:"请先选择要联系的候选人。",
  invalid_contact_shortlist:"名单超出本次目标人数或包含重复项。",
  ai_recheck_required:"AI 复核不可用，本次未发送。",
};
const greetRetryStops=new Set(["page_challenge_detected","job_context_changed","detail_cleanup_unverified"]);
function greetForm(run,shortlist) {
  return '<form data-greet-form="1" data-preview="'+escape(run.id)+'"><div class="greet-list">'+
    shortlist.map((id,index)=>'<label class="check"><input type="checkbox" name="candidate" value="'+escape(id)+'" checked>'+
      '<span>'+escape(index+1+" · "+id)+'</span></label>').join("")+
    '</div><div class="controls"><button type="submit" class="button is-primary">确认并打招呼（'+shortlist.length+' 位）</button>'+
    '<span class="subtitle">默认全选，可取消勾选</span></div></form>';
}
function greetPanel(run) {
  if(workspace.mode==="mock")return "";
  const shortlist=run.result?.shortlist || [];
  const summary=run.result?.summary || {};
  const qualified=summary.qualified ?? 0;
  const target=summary.target_contact_count ?? state.plan?.task?.target_success_count ?? null;
  const greetRuns=workspace.runs.filter(item=>item.action==="greet" && item.preview_id===run.id);
  const inline=run.result?.inline_greetings;
  const latest=Array.isArray(inline)?{status:"completed",result:{
    results:inline,candidate_ids:inline.map(item=>item.candidate_id),
    actual_successful_contacts:summary.actual_successful_contacts,
    stop_reason:inline.some(item=>["send_unconfirmed","send_state_unverified","prior_send_unconfirmed",
      "page_challenge_detected","job_context_changed","detail_cleanup_unverified"].includes(item.reason))?run.result.reason:null,
  }}:greetRuns[0];
  const head='<div class="greet-head"><h3>向符合要求的候选人打招呼</h3>'+
    (Array.isArray(inline)?'<span class="pill">边筛边发</span>':'')+
    '<span class="pill PASS">符合要求 '+escape(qualified)+' 人</span>'+
    (target!=null?'<span class="pill">目标 '+escape(target)+' 人</span>':'')+'</div>';
  if(run.result?.task_action && run.result.task_action!=="greet") {
    return qualified?'<div class="greet-block">'+head+'<p class="subtitle">本次招聘要求没有包含「打招呼」，因此不会联系候选人。若要在筛选后联系符合者，请在招聘要求里写明人数，例如「筛选后向符合条件的 3 人打招呼」。</p></div>':"";
  }
  if(latest && ["queued","running"].includes(latest.status)) {
    return '<div class="greet-block">'+head+'<p class="subtitle">正在向 '+escape(latest.candidate_count ?? shortlist.length)+
      ' 位候选人发送招呼，逐人结果会在完成后显示。</p><p class="loading-text">正在发送…</p></div>';
  }
  if(latest && latest.status==="completed") {
    const result=latest.result || {};
    const rows=(result.results || []).map(row=>'<li>'+
      (row.greeted?'<span class="pill PASS">已发送</span>':'<span class="pill UNKNOWN">未发送</span>')+
      '<span class="greet-id">'+escape(row.candidate_id)+'</span><span>'+escape(greetReasonLabels[row.reason] || row.reason || "—")+
      '</span></li>').join("");
    const stop=result.stop_reason?'，已中止：'+escape(greetReasonLabels[result.stop_reason] || result.stop_reason):"";
    const remaining=(result.results || []).filter(row=>!row.greeted && row.reason!=="already_recorded").map(row=>row.candidate_id);
    const retry=!Array.isArray(inline) && remaining.length && greetRetryStops.has(result.stop_reason)?greetForm(run,remaining):"";
    return '<div class="greet-block">'+head+'<p class="subtitle">本次成功打招呼 '+escape(result.actual_successful_contacts ?? 0)+
      ' 位，共选择 '+escape((result.candidate_ids || []).length)+' 位'+stop+'。</p>'+
      (rows?'<ul class="greet-result">'+rows+'</ul>':'')+
      (retry?'<div class="greet-retry"><p class="subtitle">以下候选人还没发送，可在页面恢复后重试。</p>'+retry+'</div>':'')+'</div>';
  }
  if(latest && latest.status==="failed") {
    return '<div class="greet-block">'+head+'<div class="notice danger"><strong>这次打招呼没有完成</strong><p>'+
      escape(greetErrorLabels[latest.error] || latest.error || "请检查 BOSS 页面连接后重试。")+'</p></div>'+greetForm(run,shortlist)+'</div>';
  }
  if(!shortlist.length) {
    return '<div class="greet-block">'+head+'<p class="subtitle">'+(qualified?
      "符合硬性要求的候选人里，暂时没有证据完整到可以安全联系的对象，本次不发起招呼。":
      "本次没有符合全部硬性要求的候选人，未发起招呼。")+'</p></div>';
  }
  return '<div class="greet-block">'+head+'<p class="subtitle">已按招聘要求筛出 '+escape(shortlist.length)+' 位证据完整的候选人'+
    (target!=null && shortlist.length<target?'（目标 '+escape(target)+' 人，当前符合者不足）':'')+
    '。确认后会逐位在 BOSS 发送招呼，发送后无法撤回。</p>'+greetForm(run,shortlist)+'</div>';
}
function formatElapsed(milliseconds) {
  if(!Number.isFinite(milliseconds) || milliseconds<=0)return "";
  const total=Math.floor(milliseconds/1000);
  const minutes=Math.floor(total/60);
  const seconds=total%60;
  return "已用 "+minutes+":"+String(seconds).padStart(2,"0");
}
function runProgress(run, offline) {
  if(!["queued","running"].includes(run.status))return "";
  const progress=run.progress || {};
  const checked=Number.isFinite(progress.checked)?progress.checked:null;
  const budget=Number.isFinite(progress.budget)?progress.budget:(state.plan?.task?.max_checks ?? null);
  const percent=checked!=null && budget?Math.min(96, Math.round(checked/budget*100)):run.status==="queued"?0:8;
  const label=progress.phase==="contact"?"候选人已通过，正在当前卡片发送招呼":
    progress.phase==="ranking"?"正在判定筛选要求":
    checked!=null?"正在核验候选人 "+checked+(budget?" / "+budget:"")+" 位":
    run.status==="queued"?"任务已提交，正在等待执行":"正在打开 BOSS 页面，准备筛选";
  const elapsed=formatElapsed(run.started?Date.now()-new Date(run.started).getTime():NaN);
  return '<div class="run-progress"><div class="run-progress-head"><span>'+(offline?"最后已知 · ":"")+escape(label)+
    (Number.isFinite(progress.greeted)?" · 已打招呼 "+escape(progress.greeted)+" 位":"")+'</span><span>'+
    escape(elapsed)+'</span></div><progress class="run-progress-bar" max="100" value="'+percent+'" aria-label="筛选进度"></progress></div>';
}
function taskRunPanel() {
  const run=workspace.runs.find(item=>item.id===state.previewRun);
  if(!run)return state.previewRun?'<p class="subtitle">正在读取本次运行状态…</p>':"";
  const summary=run.result?.summary;
  const offline=workspace.connection==="offline";
  const filterMessage={
    existing_page_filters_require_reset:"BOSS 页面仍有上次的筛选条件。请在「推荐牛人」清空后再筛一轮。",
    page_filters_unverified:"BOSS 筛选条件未通过核验，本轮已停止。请检查账号权限、页面选项和已有筛选条件。",
  }[run.result?.reason];
  const message=run.status==="completed"?(filterMessage || "本次检查了 "+(summary?.checked ?? "—")+" 位候选人。"):
    offline&&["queued","running"].includes(run.status)?"本地服务已断开，无法确认本轮是否仍在执行。":
    run.status==="running"?"本轮正在执行，可以随时中断。":
    run.status==="queued"?"任务已提交，正在等待执行。":run.error || "请重新解析并确认条件后重试。";
  const type=filterMessage?"UNKNOWN":run.status==="completed"?"PASS":run.status==="failed"?"FAIL":"UNKNOWN";
  const repeat=["completed","failed","interrupted"].includes(run.status) && state.job && state.request.trim()?
    '<button type="button" class="button" data-repeat-preview'+(state.plan?.pending?' disabled':'')+'>再筛一轮</button>':"";
  return '<section class="panel task-panel"><div class="panel-top"><div><h2>筛选预览</h2><p>'+escape(run.job || "")+
    '</p></div><span class="pill '+type+'">'+escape(filterMessage?"已停止":phases[run.status] || run.status)+'</span></div><div class="task-run-body" role="status">'+
    (offline?'<div class="notice"><strong>本地服务已断开</strong><p>下面的状态可能已经过期。恢复连接后工作台会重新核对这一轮。</p></div>':"")+
    '<p>'+escape(message)+'</p>'+runProgress(run,offline)+'<div class="controls">'+(run.status==="completed" && !filterMessage?button("查看结果","review-run",run.id,true):"")+
    (run.status==="running" && !offline?'<button type="button" class="button" data-cancel-run="'+escape(run.id)+'">中断本轮</button>':"")+repeat+'</div>'+
    (run.status==="completed" && !filterMessage?greetPanel(run):"")+
    '</div></section>';
}
function updateTaskStatus() {
  const sync=$("#task-sync");
  if(sync) sync.textContent=syncStatus();
  const syncButton=$("[data-sync-task]");
  if(syncButton)syncButton.disabled=Boolean(state.syncSubmitting ||
    workspace.runs.some(run=>run.action==="sync" && ["queued","running"].includes(run.status)));
  const target=$("#task-run");
  const panel=taskRunPanel();
  if(target && target.dataset.content!==panel) {
    target.innerHTML=panel;
    target.dataset.content=panel;
  }
  const steps=$("#task-steps");
  if(steps && steps.innerHTML!==taskSteps())steps.innerHTML=taskSteps();
  const expired=Boolean(state.plan?.snapshot && state.plan.snapshot.expires_at*1000<=Date.now());
  if(expired && !state.plan.expiryShown) {
    state.plan.expiryShown=true;
    updatePlanResult();
  }
}
function confirmAction(message,actionLabel,title="请确认操作") {
  const dialog=$("#confirm-dialog");
  if(dialog.open)return Promise.resolve(false);
  $("#confirm-title").textContent=title;
  $("#confirm-text").textContent=message;
  $("#confirm-submit").textContent=actionLabel;
  dialog.returnValue="";
  return new Promise(resolve=>{
    dialog.addEventListener("close",()=>resolve(dialog.returnValue==="ok"),{once:true});
    dialog.showModal();
  });
}
async function startPlan() {
  if(state.previewSubmitting) return;
  const input=$("#preview-count-input");
  const wanted=input?Number(input.value):null;
  const planned=state.plan?.task?.max_checks ?? 20;
  try {
    // 调整人数会生成新的草稿快照，所以顺序是：改人数 → 确认 → 开始预览
    if(state.plan?.snapshot && Number.isInteger(wanted) && wanted>0 && wanted!==planned) {
      await changeSnapshot("resize",{max_checks:wanted});
    }
    if(state.plan?.snapshot?.status!=="confirmed") await changeSnapshot("confirm");
    await startTaskPreview();
  } catch(error) {
    state.message=error.message;
    render();
  }
}
async function startTaskPreview() {
  if(state.previewSubmitting || state.previewRun)return;
  const snapshot=state.plan?.snapshot;
  const task=state.plan?.task;
  const valid=()=>state.plan?.snapshot===snapshot && snapshot?.status==="confirmed" &&
    !state.editPlan && !state.plan.plan.blockers.length && snapshot.expires_at*1000>Date.now();
  if(!valid()) {
    updatePlanResult();
    throw new Error("筛选条件未确认或已过期，请先重新解析和确认。");
  }
  state.previewSubmitting=true;
  updatePlanResult();
  try {
    const autoGreet=task.action==="greet" && state.autoGreet!==false && workspace.mode==="live";
    const confirmation=autoGreet?"筛选「"+task.job+"」时，每遇到符合条件且证据完整的候选人就发送招呼，最多成功发送 "+
      task.target_success_count+" 人。":"将按已确认的条件预览「"+task.job+"」，计划检查 "+(task.max_checks ?? 20)+" 位候选人。";
    if(!await confirmAction(confirmation,autoGreet?"开始筛选并打招呼":"开始预览"))return;
    if(!valid())throw new Error("筛选条件已变化，请重新确认。");
    const run=await post("/api/runs",{action:"preview",plan_id:snapshot.plan_id,job_id:state.job,...(autoGreet?{auto_greet:true}:{})});
    if(state.plan?.snapshot===snapshot) {
      state.previewRun=run.id;
      workspace.selectedRun=run.id;
      persistTask();
      updateTaskStatus();
    }
    await refreshWorkspace();
    if(state.page==="tasks" && state.previewRun===run.id)$("#task-run")?.scrollIntoView({block:"nearest"});
  } finally {
    state.previewSubmitting=false;
    updatePlanResult();
  }
}
function productRunList() {
  if(!workspace.runs.length)return '<p class="empty">暂无运行记录。<button class="button" data-go="tasks">前往招聘任务</button></p>';
  const query=workspace.runQuery.trim().toLowerCase();
  const rows=workspace.runs.filter(run=>[run.job,operationLabel(run),phases[run.status]].some(value=>String(value || "").toLowerCase().includes(query)));
  if(!rows.length)return '<p class="empty">没有符合查询的运行。</p>';
  const statusTone={completed:"PASS",failed:"FAIL",interrupted:"UNKNOWN"};
  return '<div class="table-wrap"><table><thead><tr><th>操作</th><th>岗位</th><th>状态</th><th>提交时间</th><th>检查人数</th><th>操作</th></tr></thead><tbody>'+
    rows.map(run=>'<tr><td>'+escape(operationLabel(run))+'</td><td>'+escape(run.job || "—")+'</td><td>'+
      '<span class="pill '+(statusTone[run.status] || "UNKNOWN")+'">'+escape(phases[run.status] || run.status)+'</span></td><td>'+escape(productDate(run.created))+
      '</td><td>'+escape(run.result?.summary?.checked ?? "—")+'</td><td><div class="controls">'+button("运行详情","run",run.id)+
      (run.action==="preview" && run.status==="completed"?button("查看结果","review-run",run.id,true):"")+
      '</div></td></tr>').join("")+'</tbody></table></div>';
}
operationPages.runs=()=>title("运行记录","查看岗位同步和筛选预览的执行状态与结果。")+
  '<section class="panel"><div class="panel-top"><h2>历史运行</h2><div class="controls"><label>搜索 <input id="run-query" type="search" value="'+
  escape(workspace.runQuery)+'" placeholder="岗位、操作或状态"></label>'+button("刷新","refresh","1")+'</div></div><div id="run-list">'+
  productRunList()+'</div></section>'+renderRun();
operationPages.settings=()=>{
  const browser=workspace.browser, mock=workspace.mode==="mock";
  const connection=browser?.mode==="demo"?"本地服务已响应，离线演示无需连接 BOSS。":
    mock?"离线演示无需连接 BOSS。":workspace.browserConnecting?"正在打开 Chrome 并检查连接…":
    browser?.recommend?"已连接 Chrome，推荐牛人页面已打开。":
    browser?.connected?"已连接 Chrome。请在打开的窗口登录 BOSS 或进入推荐牛人，再检查连接。":
    browser?"未检测到 Chrome 调试连接。":"浏览器连接待检查";
  return title("系统设置","查看岗位同步、解析方式和本地连接。")+
    actionPanel("运行状态",'<dl class="review-meta"><div><dt>模式</dt><dd>'+(mock?"离线演示 · 模拟数据":"连接模式")+
    '</dd></div><div><dt>岗位</dt><dd>'+escape(syncStatus())+'</dd></div><div><dt>解析配置</dt><dd>'+
    (workspace.configured_parser==="ai"?"已启用 AI，失败时使用本地规则":"本地规则")+'</dd></div></dl>')+
    actionPanel("连接检查",'<p role="status">'+escape(connection)+'</p><div class="controls">'+
    (!mock?button("一键打开并连接 BOSS","browser-connect","1"):"")+button("检查本地连接","browser-check","1")+
    '</div><details><summary>高级配置与诊断</summary><p>'+escape(workspace.endpoint || "")+'</p>'+
    (!mock?'<div class="controls">'+
      button("检查页面","op","diagnose")+button("测试模型连接","op","model-test")+
      button(workspace.scheduler?"暂停调度":"启动调度","schedule",workspace.scheduler?"off":"on")+button("岗位管理","go","jobs")+'</div>':"")+
      (browser?output(browser):"")+'</details>');
};
content.addEventListener("click",async event=>{
  const target=event.target.closest("[data-sync-task]");
  if(!target || state.syncSubmitting || target.disabled)return;
  state.syncSubmitting=true;
  updateTaskStatus();
  try{await post("/api/runs",{action:"sync"});await refreshWorkspace();}
  catch(error){state.message=error.message;render();}
  finally {state.syncSubmitting=false;updateTaskStatus();}
});
content.addEventListener("click",async event=>{
  const target=event.target.closest("[data-cancel-run]");
  if(!target || target.disabled)return;
  target.disabled=true;
  try {
    if(!await confirmAction("中断后本轮不会生成筛选结果，已打开的候选人页面会保留。确认中断这次筛选预览？","确认中断","中断筛选预览"))return;
    await post("/api/runs/"+encodeURIComponent(target.dataset.cancelRun)+"/cancel",{});
    await refreshWorkspace(true);
  } catch(error) {
    state.message=error.message;
    render();
  } finally {
    target.disabled=false;
  }
});
content.addEventListener("input",event=>{
  if(event.target.id==="run-query") {
    workspace.runQuery=event.target.value;
    $("#run-list").innerHTML=productRunList();
  }
});
content.addEventListener("change",event=>{
  const form=event.target.closest("[data-greet-form]");
  if(!form)return;
  const count=form.querySelectorAll('input[name="candidate"]:checked').length;
  const submit=form.querySelector('button[type="submit"]');
  if(!submit)return;
  submit.disabled=count===0;
  submit.textContent=count?"确认并打招呼（"+count+" 位）":"请选择候选人";
});
content.addEventListener("submit",async event=>{
  const form=event.target.closest("[data-greet-form]");
  if(!form)return;
  event.preventDefault();
  if(state.greetSubmitting)return;
  const candidates=[...form.querySelectorAll('input[name="candidate"]:checked')].map(input=>input.value);
  if(!candidates.length)return;
  state.greetSubmitting=true;
  const submit=form.querySelector('button[type="submit"]');
  const label=submit?.textContent;
  if(submit){submit.disabled=true;submit.textContent="正在提交…";}
  try {
    if(!await confirmAction("将向 "+candidates.length+" 位候选人发送 BOSS 招呼：\n"+candidates.join("、")+
      "\n\n发送后无法撤回，请确认已核对名单。","确认发送","确认发送 BOSS 招呼"))return;
    await queue("greet",{preview:form.dataset.preview,candidates,confirmed:true});
  } catch(error) {
    state.message=error.message;
    render();
  } finally {
    state.greetSubmitting=false;
    if(document.contains(form) && submit){submit.disabled=false;submit.textContent=label;}
  }
});
