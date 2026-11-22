const operationPages = {};
const workspace = {jobs:[], published_jobs:[], job_records:[], runs:[], scheduler:false, mode:"live", browser:null, selectedRun:"",
  conversation:"", message:"", jd:"", edit:null, connection:"pending", runQuery:""};
Object.assign(labels,{jobs:"岗位管理",runs:"运行记录",chat:"沟通与简历",jd:"JD 助手",settings:"系统设置"});
const actions={preview:"实时筛选预览",sync:"同步 BOSS 岗位",inbox:"读取未读会话","inbox-all":"读取全部会话",conversation:"查看会话",
  draft:"生成回复草稿",send:"发送消息",greet:"向已选候选人打招呼","request-resume":"请求简历",
  attachments:"查看附件","online-resume":"查看在线简历","download-resume":"下载附件",
  diagnose:"检查页面与卡片",launch:"打开 BOSS 调试浏览器",jd:"分析 JD","model-test":"测试模型连接"};
const phases={queued:"等待执行",running:"正在执行",completed:"已完成",failed:"执行失败",interrupted:"执行中断"};
const dispositions={review:"待复核",shortlisted:"本地入围",closed:"本地关闭"};
const running = () => workspace.runs.some(run=>["queued","running"].includes(run.status));
const latest = action => workspace.runs.find(run=>run.action===action && run.status==="completed")?.result;
const conversations = () => workspace.runs.find(run=>["inbox","inbox-all"].includes(run.action) && run.status==="completed")?.result?.conversations || [];
const button = (title, key, value="", primary=false) => '<button class="button '+(primary?"is-primary":"")+
  '" data-'+key+'="'+escape(value)+'">'+escape(title)+'</button>';
async function post(url, data) {
  return api(url,{method:"POST",headers:{"Content-Type":"application/json","X-Workbench":"local-ui"},body:JSON.stringify(data)});
}
async function refreshWorkspace(renderNow=false) {
  let data;
  try {data=await api("/api/workspace");workspace.connection="online";}
  catch(error){workspace.connection="offline";if(typeof updateWorkspaceStatus==="function")updateWorkspaceStatus();throw error;}
  const jobsChanged=JSON.stringify(workspace.job_records)!==JSON.stringify(data.job_records);
  const exampleChanged=workspace.example_request!==data.example_request;
  const filtersChanged=Boolean(workspace.boss_page_filter_options)!==Boolean(data.boss_page_filter_options);
  Object.assign(workspace,data);
  if (typeof reviewWorkspaceReady==="function") reviewWorkspaceReady();
  if (state.job && !workspace.job_records.some(job=>job.job_id===state.job)) {
    state.job="";
    scheduleTaskParse(false);
  }
  if (renderNow || ((jobsChanged || exampleChanged || filtersChanged) && state.page==="tasks")) render();
  else if ($("#run-list") && typeof productRunList==="function") $("#run-list").innerHTML=productRunList();
  if(typeof updateWorkspaceStatus==="function")updateWorkspaceStatus();
}
function actionPanel(name,body) {
  return '<section class="panel"><div class="panel-top"><h2>'+name+'</h2></div><div class="form">'+body+'</div></section>';
}
function output(value) {
  return '<pre class="response">'+escape(JSON.stringify(value,null,2))+'</pre>';
}
function selectedRun() {return workspace.runs.find(run=>run.id===workspace.selectedRun);}
function renderRun() {
  const item=selectedRun();
  if (!item) return "";
  let body=item.error?'<p class="danger">'+escape(item.error)+'</p>':item.result?
    '<details><summary>高级运行详情</summary>'+output(item.result)+'</details>':'<p>'+escape(phases[item.status])+'</p>';
  if (item.action==="preview" && item.result) body=button("在候选人页复核","review-run",item.id)+body;
  if (item.action==="draft" && item.result?.body) body=button("填入消息编辑框","use-draft",item.id)+body;
  if (workspace.mode!=="mock" && item.action==="preview" && item.result?.shortlist?.length) {
    body='<form id="greet-form" data-preview="'+escape(item.id)+'"><fieldset><legend>复核后选择本次联系对象</legend>'+
      item.result.shortlist.map((id,index)=>'<label class="check"><input type="checkbox" name="candidate" value="'+escape(id)+'">候选人 '+
      (index+1)+' · '+escape(id)+'</label>').join("")+
      '<button type="submit" class="button is-primary">确认并打招呼</button></fieldset></form>'+body;
  }
  return actionPanel(escape(actions[item.action])+" · "+escape(item.id),body);
}
operationPages.jobs=()=>{
  const jobs=workspace.jobs, job=workspace.edit||{name:"",request:"",enabled:false,interval:0};
  const rows=jobs.map(j=>'<tr><td>'+escape(j.name)+'</td><td>'+escape(j.enabled?"启用":"停用")+
    '</td><td>'+escape(j.interval?j.interval+" 分钟":"手动")+'</td><td>'+
    button("编辑","edit-job",j.id)+' '+button("筛选预览","preview-job",j.id,true)+'</td></tr>').join("");
  return title("岗位管理","同步推荐牛人的岗位，维护各岗位的筛选条件与运行计划。")+
    '<section class="panel"><div class="panel-top"><h2>岗位列表</h2><div class="controls">'+button(actions.sync,"op","sync")+
    button("新增岗位","edit-job","new",true)+'</div></div>'+
    (rows?'<div class="table-wrap"><table><thead><tr><th>岗位</th><th>本地状态</th><th>调度</th><th>操作</th></tr></thead><tbody>'+rows+
    '</tbody></table></div>':'<p class="empty">暂无岗位。可同步 BOSS 岗位或添加本地岗位。</p>')+'</section>'+
    '<section class="panel"><div class="panel-top"><h2>'+(job.id?"编辑岗位":"新增岗位")+'</h2></div><form class="form" id="job-form">'+
    '<input name="id" type="hidden" value="'+escape(job.id)+'"><label>岗位名称<input name="name" required value="'+escape(job.name)+
    '"></label><label>筛选要求<textarea name="request" rows="3">'+escape(job.request)+
    '</textarea></label><label>预览间隔（分钟，0 为手动）<input name="interval" type="number" min="0" max="1440" value="'+escape(job.interval)+
    '"></label><label class="check"><input name="enabled" type="checkbox" '+(job.enabled?"checked":"")+
    '>允许按计划生成预览</label><button class="button is-primary" type="submit">保存岗位</button></form></section>';
};
function dispositionControl(id) {
  const key=(state.reports[state.report]?.stamp || "当前报告")+":"+id;
  const value=workspace.dispositions?.[key] || "review";
  return '<label class="review-status">本地复核状态<select id="disposition" data-record="'+escape(key)+'">'+
    Object.entries(dispositions).map(([status,label])=>'<option value="'+status+'" '+(value===status?"selected":"")+
    '>'+label+'</option>').join("")+'</select></label>';
}
$("#detail").addEventListener("change",async event=>{
  if(event.target.id!=="disposition")return;
  try {
    await post("/api/disposition",{id:event.target.dataset.record,status:event.target.value});
    await refreshWorkspace();
  } catch(error){state.message=error.message;$("#detail").close();render();}
});
operationPages.chat=()=>{
  const inbox=conversations();
  const chosen=inbox.find(row=>row.dataId===workspace.conversation);
  const options=inbox.map(row=>'<option value="'+escape(row.dataId)+'" '+(row.dataId===workspace.conversation?"selected":"")+'>'+
    escape(row.candidateName||row.dataId)+" · "+escape(row.jobTitle||"")+'</option>').join("");
  return title("沟通与简历","读取当前未读会话，查看简历附件或生成草稿。")+
    actionPanel("会话列表",'<div class="controls">'+button(actions.inbox,"op","inbox",true)+button(actions["inbox-all"],"op","inbox-all")+'</div>'+
      (options?'<label>会话<select id="conversation-target"><option value="">请选择</option>'+options+'</select></label>':"")+
      (chosen?'<div class="controls">'+["conversation","draft","online-resume","attachments","download-resume","request-resume"].map(a=>button(actions[a],"chat-op",a)).join("")+
      '</div><label>消息正文<textarea id="chat-message" rows="4">'+escape(workspace.message)+'</textarea></label>'+
      button("确认并发送消息","chat-op","send",true):""))+renderRun();
};
operationPages.jd=()=>title("JD 助手","从岗位描述提炼要求和需要澄清的问题。")+
  actionPanel("岗位描述",'<form id="jd-form"><label>岗位描述<textarea id="jd-input" rows="5" required>'+escape(workspace.jd)+
  '</textarea></label><button class="button is-primary" type="submit">分析岗位描述</button></form>')+renderRun();
async function queue(action,payload={}) {
  const run=await post("/api/runs",{action,...payload});
  workspace.selectedRun=run.id;
  await refreshWorkspace(true);
  return run;
}
document.addEventListener("click",async event=>{
  const data=event.target.closest("button")?.dataset;
  if(!data) return;
  try {
    if(Object.hasOwn(data,"repeatPreview")){
      const run=workspace.runs.find(item=>item.id===state.previewRun);
      if(!["completed","failed","interrupted"].includes(run?.status) || state.plan?.pending)return;
      await parseTask();
      if(state.page==="tasks")$("#plan-result")?.scrollIntoView({block:"nearest"});
    }
    if(data.run) {workspace.selectedRun=data.run;render();}
    if(data.reviewRun){
      const run=workspace.runs.find(item=>item.id===data.reviewRun), result=run?.result;
      if(run?.plan_id && typeof openReviewRun==="function") {
        navigate("review");
        await openReviewRun(run.id);
        return;
      }
      const rows=["ranked_candidates","rejected_candidates","review_candidates","capture_failed_candidates"]
        .flatMap(key=>result?.[key]||[]).map((row,index)=>({...row,id:"R"+String(index+1).padStart(2,"0"),
          eligibility:row.eligibility_status||row.hard_filter_status||"CAPTURE_FAILED"}));
      if(!result)throw new Error("运行没有可读取的结果");
      const stats=result.summary;
      state.reports.unshift({stamp:run.created+" · "+run.id,data:{job:result.job,as_of:run.created.slice(0,10),
        rows,checked:stats.checked,counts:{PASS:stats.qualified,FAIL:stats.unqualified,
          UNKNOWN:stats.needs_review,CAPTURE_FAILED:stats.capture_failed}}});
      state.report=0;state.status="";state.page="candidates";render();
    }
    if(data.useDraft){
      workspace.message=workspace.runs.find(item=>item.id===data.useDraft)?.result?.body||"";
      render();
    }
    if(data.editJob) {workspace.edit=workspace.jobs.find(j=>j.id===data.editJob)||null;render();}
    if(data.previewJob) {
      const job=workspace.jobs.find(j=>j.id===data.previewJob);
      if(!job?.request) throw new Error("请先为岗位保存筛选要求");
      const matches=workspace.job_records.filter(record=>record.job_name===job.name);
      if(matches.length!==1) throw new Error("请在任务解析页选择可核验身份的岗位");
      state.job=matches[0].job_id;
      state.request=job.request;
      navigate("tasks");
      scheduleTaskParse(true);
    }
    if(data.runPlan){
      await startPlan();
    }
    if(data.launchPage)await queue("launch",{page:data.launchPage});
    if(data.op) await queue(data.op);
    if(data.browserConnect && !workspace.browserConnecting){
      workspace.browserConnecting=true;
      render();
      try {workspace.browser=await post("/api/browser/connect",{});}
      finally {workspace.browserConnecting=false;render();}
    }
    if(data.browserCheck){workspace.browser=await api("/api/browser");render();}
    if(data.refresh)await refreshWorkspace(true);
    if(data.schedule){await post("/api/scheduler",{enabled:data.schedule==="on"});await refreshWorkspace(true);}
    if(data.chatOp){
      const action=data.chatOp, target=workspace.conversation;
      if(!target)throw new Error("请先选择会话");
      const write=["send","request-resume"].includes(action);
      const name=conversations().find(row=>row.dataId===target)?.candidateName||target;
      if(write&&!await confirmAction("确认对 "+name+" 执行「"+actions[action]+"」？"+(action==="send"?"\n\n"+workspace.message:""),"确认操作"))return;
      await queue(action,{candidate:target,message:workspace.message,confirmed:write});
    }
  }catch(error){state.message=error.message;render();}
});
content.addEventListener("input",event=>{
  if(event.target.id==="jd-input")workspace.jd=event.target.value;
  if(event.target.id==="chat-message")workspace.message=event.target.value;
});
content.addEventListener("change",event=>{
  if(event.target.id==="conversation-target"){workspace.conversation=event.target.value;render();}
});
content.addEventListener("submit",async event=>{
  const form=event.target;
  if(!["job-form","jd-form","greet-form"].includes(form.id))return;
  event.preventDefault();
  try {
    if(form.id==="job-form"){
      const data=new FormData(form);
      await post("/api/jobs",{name:data.get("name"),request:data.get("request"),id:data.get("id"),
        interval:Number(data.get("interval")),enabled:data.has("enabled")});
      workspace.edit=null;await refreshWorkspace(true);
    }
    if(form.id==="jd-form")await queue("jd",{request:workspace.jd});
    if(form.id==="greet-form"){
      const candidates=new FormData(form).getAll("candidate");
      if(!candidates.length)throw new Error("请选择候选人");
      if(await confirmAction("确认向这 "+candidates.length+" 位已选候选人打招呼？","确认发送"))await queue("greet",{
        candidates,preview:form.dataset.preview,confirmed:true});
    }
  }catch(error){state.message=error.message;render();}
});
refreshWorkspace().catch(()=>{});
setInterval(async ()=>{
  const wasRunning=running(), active=selectedRun()?.status;
  try {
    await refreshWorkspace();
    const finished=active && ["queued","running"].includes(active) && !["queued","running"].includes(selectedRun()?.status);
    if((wasRunning&&!running()||finished)&&["runs","chat","jd","settings","jobs"].includes(state.page))render();
  } catch {}
},2500);
