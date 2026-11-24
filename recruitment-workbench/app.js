const $ = selector => document.querySelector(selector);
const content = $("#content");
const labels = {overview:"工作总览", tasks:"招聘任务", candidates:"已导入的报告", catalog:"专业目录"};
const statusText = {PASS:"符合", FAIL:"不符合", UNKNOWN:"不符合", CAPTURE_FAILED:"采集失败"};
const fields = {education_level:"学历", education_mode:"学习形式", graduation_year:"毕业届别",
  graduate_status:"应届身份", major:"专业", major_group:"专业类", hr_experience_months:"人力经历（月）",
  hands_on_tools:"实操工具", OR:"满足任一专业条件", AND:"全部满足"};
const state = {autoGreet:true, bossFilterOpen:false, page:"tasks", reports:[], report:0, catalog:null, status:"", query:"", discipline:"",
  catalogPage:0, request:"",
  job:"", plan:null, message:""};

function escape(value) {
  return String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[ch]);
}
async function api(url, options) {
  let response;
  try {response=await fetch(url,options);}
  catch(error) {
    if(error.name==="AbortError")throw error;
    throw new Error("无法连接工作台，请检查本地服务后重试。");
  }
  const data = await response.json().catch(()=>({}));
  if (!response.ok) throw new Error(data.error || "读取失败");
  return data;
}
function activeReport() { return state.reports[state.report]?.data; }
function candidates() { return activeReport()?.rows || activeReport()?.anonymous_rows || []; }
function pill(status) {return '<span class="pill '+ escape(status) +'">'+escape(statusText[status] || status || "—")+'</span>';}
function title(name, description) {
  return '<div class="page-title"><h1>'+escape(name)+'</h1><p class="subtitle">'+escape(description)+'</p></div>';
}
function fieldStatus(row, field) {
  return row.hard_filters?.find(item => item.field === field)?.status || "—";
}
function table(rows) {
  if (!rows.length) return '<div class="empty">没有匹配的候选人记录。</div>';
  const headings = ["候选人","整体判断","毕业届别","专业范围","学习形式","学历"];
  return '<div class="table-wrap"><table><thead><tr>'+headings.map(x=>"<th>"+x+"</th>").join("")+
    '</tr></thead><tbody>'+rows.map(({id,eligibility, ...row})=>'<tr><td><button data-id="'+escape(id)+'">'+escape(id)+
    '</button></td><td>'+pill(eligibility)+'</td>'+
    ["graduation_year","OR","education_mode","education_level"].map(key=>"<td>"+pill(fieldStatus(row,key))+"</td>").join("")+
    "</tr>").join("")+'</tbody></table></div>';
}
function overview() {
  const data=activeReport();
  const counts=data?.counts || {};
  const stats=[["已检查",data?.checked ?? data?.checked_records ?? "—",""],["符合",counts.PASS ?? "—","pass"],
    ["证据不足",counts.UNKNOWN ?? "—","unknown"],["不符合",counts.FAIL ?? "—",""],["采集失败",counts.CAPTURE_FAILED ?? "—",""]];
  return '<section class="hero"><div><span class="eyebrow">岗位筛选</span><h1>'+escape(data?.job || "招聘工作台")+
    '</h1><p>'+escape(data ? "筛选记录 · "+(data.as_of || state.reports[state.report].stamp) : "导入一份筛选报告后查看候选人和证据。")+
    '</p></div><button class="button is-primary" data-go="tasks">新建解析</button></section><div class="stats">'+
    stats.map(([name,count,css])=>'<div class="stat '+css+'"><span>'+name+'</span><strong>'+escape(count)+'</strong></div>').join("")+
    '</div><section class="panel"><div class="panel-top"><div><h2>候选人复核</h2><p>查看硬条件判断与简历引文</p></div>'+
    '<button class="button" data-go="candidates">查看全部</button></div>'+table(candidates().slice(0,8))+'</section>';
}
function candidatePage() {
  const options = state.reports.map((r,i)=>'<option value="'+i+'"'+(i===state.report?' selected':'')+'>'+escape(r.stamp)+'</option>').join("");
  const rows=candidates().filter(r=>!state.status || r.eligibility===state.status);
  return title("候选人",activeReport()?.job || "筛选报告中的匿名候选人")+
    '<section class="panel"><div class="panel-top"><h2>筛选记录 · '+rows.length+
    '</h2><div class="controls"><select id="report" aria-label="筛选报告">'+options+
    '</select><select id="status" aria-label="筛选状态"><option value="">全部状态</option>'+
    Object.entries(statusText).map(([code,label])=>'<option value="'+code+'"'+(state.status===code?' selected':'')+'>'+label+'</option>').join("")+
    '</select></div></div>'+table(rows)+'</section>';
}
function condition(item) {
  if (item.conditions) {
    const groupLabels={OR:"满足任一项",AND:"全部满足",NOT:"排除"};
    return (groupLabels[item.operator] || item.operator)+"（"+item.conditions.map(condition).join("；")+"）";
  }
  const valueLabels={secondary:"中专",associate:"大专",bachelor:"本科",master:"硕士",
    doctorate:"博士",full_time:"全日制",part_time:"非全日制",fresh_graduate:"应届毕业生"};
  const operators={gt:"超过",lte:"不超过",gte:"至少",equals:"为",one_of:"为以下任一项",has_any:"至少一项",between:"为"};
  const extraFields={job_related_preference:"岗位偏好",job_related_requirement:"岗位要求",
    tools:"工具",skills:"技能",certificates:"证书",industry_experience:"行业经验",experience_months:"工作经验（月）"};
  const field=item.field.startsWith("experience_months:")?
    item.field.slice("experience_months:".length)+"经验（月）":fields[item.field] || extraFields[item.field] || item.field;
  if(item.operator==="verify") return "需核实"+field+"："+item.value;
  const value=Array.isArray(item.value)?item.value.join(item.operator==="between"?"–":"、"):
    Object.hasOwn(valueLabels,item.value)?valueLabels[item.value]:item.value;
  return field+" "+(operators[item.operator] || item.operator)+" "+value;
}
function planResult() {
  const output=state.plan;
  if(output?.pending) return '<p class="loading-text" role="status">正在解析招聘要求…</p>';
  if(output?.error) return '<div class="notice danger" role="alert"><strong>解析失败</strong><p>'+escape(output.error)+'</p>'+
    (/模型解析未通过/.test(output.error)?'<button type="button" class="button" data-force-rule>改用规则解析继续</button>':"")+'</div>';
  if(!output?.task) return "";
  const t=output.task;
  const metadata=output.parsing_metadata;
  const fallbackLabels={timeout:"模型请求超时",service_error:"模型接口暂不可用",invalid_json:"模型返回的 JSON 无效",
    invalid_schema:"模型返回的结构不符合契约",invalid_source:"模型给出的原文位置无法核对",
    ungrounded_condition:"模型提出了原文无法支持的条件",incomplete_output:"模型遗漏了已识别的条件",
    empty_output:"模型未返回内容",missing_api_key:"未配置模型密钥",missing_model:"未配置解析模型",missing_endpoint:"未配置模型接口地址",
    invalid_timeout:"模型超时配置无效"};
  const modeLabels={rule:"本地规则",ai:"AI 解析（已核对原文）",rule_fallback:"本地规则（模型暂不可用）",human_override:"人工修订"};
  const mode=metadata?.mode==="ai" && metadata.model_strategy==="verified_choices"?
    "模型 API 逐项核对（已关联原文）":modeLabels[metadata?.mode];
  const blockerLabels={criteria_required:"请补充至少一条可识别的招聘条件",
    unsupported_conditions:"原文含有系统无法处理的条件",sensitive_criteria:"请检查涉及敏感信息的条件",
    target_success_count_required:"请明确需要联系的人数",
    target_success_count_exceeds_max_checks:"联系目标人数超过了计划检查人数，请把检查人数调大或降低目标人数",
    conflicting_action:"请确认是否需要联系候选人",
    conflicting_max_checks:"原文包含不同的检查上限",conflicting_target_success_count:"原文包含不同的招呼目标人数",
    conflicting_hr_experience_months:"原文中的人力资源经验上下限互相冲突",
    source_evidence_incomplete:"部分条件缺少可定位的原文依据，请补充招聘要求后重新解析。",
    preview_requires_matching_rules:"当前预览仍使用原有规则。解析结果与执行规则有差异，本次只能复核解析结果。"};
  const unsupported=t.unsupported_conditions || [];
  const pendingList=output.plan.blockers.flatMap(item=>{
    if(item==="unsupported_conditions" && unsupported.length) {
      return unsupported.map(text=>"请从招聘要求中移除不支持的条件「"+text+"」，再重新解析。");
    }
    return [item.startsWith("unresolved_condition:")?
      "请明确："+item.slice("unresolved_condition:".length):blockerLabels[item] || item];
  });
  const agentHints=output.plan.blockers.filter(item=>item.startsWith("unresolved_condition:"))
    .map(item=>item.slice("unresolved_condition:".length))
    .filter(text=>/agent|workbuddy/i.test(text) && /经验|经历|实操|项目/.test(text));
  const examples=agentHints.map(text=>'<div class="task-example"><p>「'+escape(text)+
    '」请说明是使用工具还是交付项目，以及是否为硬性要求。选择下面一种写法：</p>'+
    [['工具实操必需','必须有 AI Agent 实操经验'],['工具实操优先','有 AI Agent 实操经验优先'],
     ['项目交付必需','必须有 AI Agent 项目交付经历']].map(([label,replacement])=>
      '<p><button class="button" type="button" data-replace-request="'+escape(text)+'" data-replacement="'+
      escape(replacement)+'">'+label+'</button> '+escape(replacement)+'</p>').join("")+'</div>').join("");
  const renderList=items=>items.length?'<ul>'+items.map(x=>"<li>"+escape(x)+"</li>").join("")+"</ul>":'<p class="subtitle">无</p>';
  const modeLabel={ai:"模型解析",rule:"本地规则解析",human_override:"人工修订"}[mode] || mode;
  const modeNotice=mode?'<p class="subtitle">'+escape(modeLabel)+
    (metadata.fallback_reason?' · '+escape(fallbackLabels[metadata.fallback_reason] || "AI 解析未通过校验"):'')+'</p>':'';
  const snapshot=output.snapshot;
  const expired=snapshot && snapshot.expires_at*1000<=Date.now();
  const confirmed=snapshot?.status==="confirmed";
  const count=t.max_checks ?? 20;
  const evidence=[...new Set((metadata?.criteria_sources || []).flatMap(item=>item.sources.map(source=>source.source_text)))];
  const earlierEvidence=[...new Set((metadata?.previous_criteria_sources || []).flatMap(item=>item.sources.map(source=>source.source_text)))];
  const busy=output.changing?" disabled":"";
  const canRun=snapshot && !expired && !pendingList.length && !state.editPlan && !state.previewRun;
  const autoNote=state.autoGreet!==false && typeof workspace!=="undefined" && workspace.mode!=="mock"?
    "筛选完成后会自动向符合要求的人打招呼（最多 "+escape(t.target_success_count ?? 10)+" 人）":"";
  const actionBar=canRun?
    '<div class="plan-actions">'+
      '<label class="plan-count">计划检查人数 <input id="preview-count-input" type="number" min="1" max="500" step="1" value="'+escape(count)+'"'+(output.changing||state.previewSubmitting?' disabled':'')+'></label>'+
      '<div class="plan-buttons">'+
        '<button type="button" class="button is-primary" data-run-plan="1"'+(output.changing||state.previewSubmitting?' disabled':'')+'>'+
          (state.previewSubmitting?"正在提交…":confirmed?"开始筛选":"确认并开始筛选")+'</button>'+
        '<button type="button" class="button" data-edit-plan="1"'+busy+'>修改条件</button>'+
      '</div>'+
      (autoNote?'<p class="plan-note">'+autoNote+'</p>':'')+
    '</div>':"";
  const editor=state.editPlan && snapshot && !expired && typeof conditionEditor==="function"?conditionEditor():'';
  const criteria=(items,category)=>items.length?'<ul class="requirement-list">'+items.map(item=>{
    const source=item.source;
    const leaves=node=>node.conditions?node.conditions.flatMap(leaves):[node];
    const matching=(metadata?.criteria_sources || []).filter(ref=>ref.category===category && leaves(item).some(leaf=>
      ref.field===leaf.field && ref.operator===leaf.operator && JSON.stringify(ref.value)===JSON.stringify(leaf.value)));
    const quotes=[...new Set(matching.flatMap(ref=>ref.sources.map(s=>s.source_text)))];
    if(!quotes.length && source && source!=="人工修订")quotes.push(source);
    return '<li><strong>'+escape(condition(item))+'</strong>'+
      (quotes.length?'<details><summary>原文依据</summary>'+renderList(quotes)+'</details>':'')+'</li>';
  }).join("")+'</ul>':'<p class="subtitle">暂无</p>';
  return '<section class="condition-summary"><div class="section-heading"><div><h2>确认筛选条件</h2>'+modeNotice+
    '<p class="subtitle">'+t.hard_filters.length+' 项核心要求 · '+t.soft_preferences.length+' 项加分项 · 判定：'+
      escape(policyLabel({decision_mode:t.decision_mode,min_core_percent:t.min_core_percent,min_bonus_count:t.min_bonus_count}))+'</p>'+
    '</div><span class="pill '+(pendingList.length?"UNKNOWN":"PASS")+'">'+(pendingList.length?"需要补充信息":"解析完成")+'</span></div>'+
    (Object.keys(t.boss_page_filters || {}).length?'<div class="plan-filter-row"><span>BOSS 页面筛选</span>'+
      Object.entries(t.boss_page_filters).map(([key,value])=>'<b>'+escape(key+" "+value)+'</b>').join("")+'</div>':"")+
    '<div class="requirements-grid'+(t.hard_filters.length && t.soft_preferences.length?"":" is-single")+'">'+
    (t.hard_filters.length?'<div><h3>核心要求</h3>'+criteria(t.hard_filters,"hard")+'</div>':"")+
    (t.soft_preferences.length?'<div><h3>加分项</h3>'+criteria(t.soft_preferences,"preference")+'</div>':"")+
    '</div>'+
    ((t.reference_conditions || []).length?'<div class="notice"><strong>以下条件暂未自动识别，未参与判定</strong>'+
      renderList(t.reference_conditions)+'<p>建议改写成可核对的条件，例如「1 年以上电商行业经验」「做过 RAG 项目」。</p>'+
      '<button type="button" class="button" data-rewrite-refs>自动改写这些条件</button></div>':'')+
    (state.rewriteNote?'<p class="muted rewrite-note">'+escape(state.rewriteNote)+'</p>':'')+
    (pendingList.length?'<div class="notice"><strong>暂时无法进入运行预览</strong>'+renderList(pendingList)+
      (examples || '<p>请把需要补充的条件写具体，并标明是必须满足还是优先考虑。</p>')+
      '<button type="button" class="button" data-edit-request="1">返回修改招聘要求</button></div>':'')+
    (evidence.length?'<details><summary>查看全部原文依据</summary>'+renderList(evidence)+'</details>':'')+
    (earlierEvidence.length?'<details><summary>查看修订前的原文依据</summary>'+renderList(earlierEvidence)+'</details>':'')+
    (snapshot?.task_hash?'<details><summary>高级详情</summary><p class="digest">规则哈希 '+escape(snapshot.task_hash)+'</p></details>':'')+
    (output.actionError?'<p class="danger" role="alert">'+escape(blockerLabels[output.actionError] || output.actionError)+'</p>':'')+
    (expired?'<p class="danger">规则快照已过期，请重新解析。</p>':'')+
    (state.editPlan?editor:actionBar)+'</section>';
}
function taskSteps() {
  const snapshot=state.plan?.snapshot;
  const run=typeof workspace==="undefined"?null:workspace.runs.find(item=>item.id===state.previewRun);
  const greeted=run && workspace.runs.some(item=>item.action==="greet" && item.preview_id===run.id);
  const current=greeted?5:run?(run.status==="completed"?4:3):snapshot?.status==="confirmed" && snapshot.expires_at*1000>Date.now()?3:state.plan?.task?2:1;
  return ["填写需求","确认条件","运行预览","查看结果","打招呼"].map((label,i)=>
    '<li class="'+(i+1===current?"current":i+1<current?"complete":"")+'"'+(i+1===current?' aria-current="step"':'')+
    '><span>'+(i+1)+'</span>'+label+'</li>').join("");
}
function bossPageFilterEditor() {
  const options=Object.entries(workspace.boss_page_filter_options || {});
  if(!options.length)return "";
  const selectedMajors=(state.bossPageFilters["专业"] || "").split(",").filter(Boolean);
  const majorGroups=Object.entries(workspace.boss_major_groups || {});
  const majorEditor=majorGroups.length?'<details class="boss-major-editor"><summary>专业 · <span id="boss-major-count">'+
    (selectedMajors.length?"已选 "+selectedMajors.length+" / 5":"不限")+'</span></summary>'+
    '<p>最多选 5 项。若选项还未出现在 BOSS 快捷列表，筛选时会更新当前岗位的专业快捷项。</p><p id="boss-major-selection" role="status">'+
    escape(selectedMajors.join("、"))+'</p>'+
    '<label class="boss-major-search">搜索专业 <input id="boss-major-search" type="search" autocomplete="off" placeholder="输入专业名称，如 计算机、心理"></label>'+
    majorGroups.map(([group,names])=>
      '<details><summary>'+escape(group)+'</summary><div class="boss-major-options">'+names.map(name=>
        '<label><input type="checkbox" data-boss-major value="'+escape(name)+'"'+
        (selectedMajors.includes(name)?' checked':'')+'>'+escape(name)+'</label>').join("")+'</div></details>').join("")+'</details>':"";
  return '<details class="task-example boss-page-filters"'+
    (state.bossFilterOpen || Object.keys(state.bossPageFilters).length?' open':'')+
    '><summary>BOSS 页面筛选</summary><p>选项需要 BOSS 账号具备相应权限。开始筛选前会核验所选条件。</p>'+
    '<div class="requirements-grid">'+options.filter(([key])=>(key!=="专业" || !majorEditor) && key!=="只看第一学历").map(([key,values])=>'<label>'+escape(key)+
      (key==="年龄"?'<input data-boss-page-filter="年龄" type="text" maxlength="5" pattern="[0-9]{2}-[0-9]{2}" '+
        'placeholder="不限，例如 25-35" value="'+escape(state.bossPageFilters[key] || "")+'"></label>':
      '<select data-boss-page-filter="'+escape(key)+'"><option value="">不限</option>'+
      values.map(value=>'<option value="'+escape(value)+'"'+(state.bossPageFilters[key]===value?' selected':'')+'>'+
        escape(value)+'</option>').join("")+'</select></label>')).join("")+'</div>'+
    '<label class="check boss-first-degree"'+(state.bossPageFilters["院校"]==="公办本科"?"":" hidden")+'>'+
    '<input type="checkbox" data-boss-first-degree '+(state.bossPageFilters["只看第一学历"]==="是"?"checked":"")+
    '><span>只看第一学历（第一学历为全日制本科）</span></label>'+majorEditor+'</details>';
}
function policyLabel(policy) {
  const mode=policy?.decision_mode || "strict_all";
  if(mode==="strict_core")return "核心要求全部满足就判为符合";
  if(mode==="weighted")return "核心满足 "+(policy.min_core_percent ?? 60)+"%、加分项 "+(policy.min_bonus_count ?? 1)+" 项即可";
  return "所有条件都满足才判为符合";
}
function policyHint(mode) {
  if(mode==="strict_core")return "核心要求全部满足就通过；加分项只影响排序，不卡通过。";
  if(mode==="weighted")return "核心要求可以差一点，用加分项补：达到下面的门槛就通过。";
  return "核心和加分项都要满足，最严格。";
}
function jobPolicyPanel() {
  if(typeof workspace==="undefined" || !state.job)return "";
  const policy=(workspace.job_policies || {})[state.job] || {decision_mode:"strict_all",min_core_percent:100,min_bonus_count:0};
  const mode=policy.decision_mode || "strict_all";
  const core=policy.min_core_percent ?? 100;
  const bonus=policy.min_bonus_count ?? 0;
  return '<details class="job-policy" id="job-policy"><summary>判定规则：'+escape(policyLabel(policy))+'</summary>'+
    '<div class="policy-body">'+
      '<label class="policy-mode">哪些人算符合？<select id="policy-mode">'+
        [["strict_core","核心要求全部满足（推荐）"],["strict_all","所有条件都满足（最严）"],["weighted","核心差一点，加分项补足"]]
          .map(([value,text])=>'<option value="'+value+'"'+(mode===value?' selected':'')+'>'+escape(text)+'</option>').join("")+
      '</select></label>'+
      '<p class="policy-hint" id="policy-hint">'+escape(policyHint(mode))+'</p>'+
      '<div class="policy-weighted"'+(mode==="weighted"?"":" hidden")+' id="policy-weighted">'+
        '<label>核心至少满足 <input id="min-core" type="number" min="0" max="100" value="'+escape(core)+'"> %</label>'+
        '<label>加分项至少 <input id="min-bonus" type="number" min="0" max="20" value="'+escape(bonus)+'"> 项</label>'+
      '</div>'+
      '<div class="policy-actions"><button type="button" class="button is-primary" data-save-policy>保存规则</button>'+
      '<span id="policy-status" role="status"></span></div>'+
    '</div></details>';
}
function tasks() {
  const jobs=typeof workspace==="undefined"?[]:workspace.job_records;
  const options=jobs.map(job=>{
    const duplicate=jobs.some(other=>other.job_id!==job.job_id && other.job_name===job.job_name);
    return '<option value="'+escape(job.job_id)+'"'+(job.job_id===state.job?' selected':'')+'>'+
      escape(job.job_name+(duplicate?' · '+job.source_identity:''))+'</option>';
  }).join("");
  const example=typeof workspace!=="undefined" && workspace.example_request
    ?workspace.example_request:"本科及以上，计算机类专业，人力资源经验不超过一年";
  const sync=typeof syncStatus==="function"?syncStatus():"";
  return title("招聘任务","填写岗位要求，核对筛选条件，再运行候选人预览。")+
    '<ol id="task-steps" class="task-steps" aria-label="招聘任务步骤">'+taskSteps()+'</ol>'+
    '<section class="panel task-panel"><div class="panel-top"><div><h2>填写招聘需求</h2><p id="task-sync" role="status">'+escape(sync)+
    '</p></div><button type="button" class="button" data-sync-task>同步岗位</button></div>'+
    '<form class="form" id="plan"><label>招聘岗位<select id="job" required><option value="">请先选择岗位</option>'+
    options+'</select></label><div id="job-policy-slot">'+jobPolicyPanel()+'</div>'+(jobs.length?'':'<p class="notice">暂无岗位。请先点击「同步岗位」。</p>')+
    '<label>核心要求（每行一条，必须满足）<textarea id="core-lines" rows="3" placeholder="例如：本科及以上\n1 年以上电商行业经验">'+
      escape(state.coreLines)+'</textarea></label>'+
    '<label>加分项（每行一条，优先考虑）<textarea id="bonus-lines" rows="2" placeholder="例如：会 WorkBuddy 或 Agent\n做过 RAG 项目">'+
      escape(state.bonusLines)+'</textarea></label>'+
    '<input type="hidden" id="request" value="'+escape(state.request)+'">'+
    '<p class="muted">逐条填写会分别标注「必须满足 / 优先考虑」；暂时无法识别的条件会列在下方参考条件里，不会卡住流程。</p>'+
    '<details class="task-example"><summary>查看填写示例</summary><p>'+escape(example)+'</p>'+
    '<button type="button" class="button" data-use-example>使用示例</button></details>'+
    bossPageFilterEditor()+
    (typeof workspace!=="undefined" && workspace.mode!=="mock"
      ?'<label class="check auto-greet"><input type="checkbox" id="auto-greet" '+(state.autoGreet?'checked':'')+
       '><span>筛选完成后自动向符合要求的人打招呼</span></label>':"")+
    '<button class="button is-primary" id="parse-button" type="submit" '+(!state.job || state.plan?.pending?'disabled':'')+'>'+
    (state.plan?.pending?"正在解析…":state.plan?.task?"重新解析":"解析招聘要求")+'</button></form></section>'+
    '<div id="plan-result">'+planResult()+'</div><div id="task-run">'+(typeof taskRunPanel==="function"?taskRunPanel():"")+'</div>';
}
function catalog() {
  return title("专业目录","查找本科专业，查看其专业类和学科门类。")+
    '<section class="panel"><div class="panel-top"><div><h2>教育部本科专业目录</h2><p id="catalog-count"></p></div>'+
    '<div class="controls"><input id="query" type="search" placeholder="搜索专业、专业类、代码" aria-label="搜索专业" value="'+escape(state.query)+
    '"><select id="discipline" aria-label="学科门类"><option value="">全部门类</option>'+
    Object.entries(state.catalog?.disciplines || {}).map(([code,label])=>'<option value="'+code+'"'+(code===state.discipline?' selected':'')+'>'+escape(label)+'</option>').join("")+
    '</select></div></div><div id="catalog-results"></div></section>';
}
function listCatalog() {
  if(!state.catalog) {$("#catalog-results").innerHTML='<p class="empty">专业目录尚未加载。</p>';return;}
  const data=state.catalog, input=state.query.trim().toLowerCase();
  const rows=Object.entries(data.majors).filter(([code,name]) => {
    const discipline=data.disciplines[code.slice(0,2)], group=data.groups[code.slice(0,4)];
    return (!state.discipline || code.startsWith(state.discipline)) &&
      [name,code,discipline,group,data.aliases[input]===name?input:""].some(item=>String(item || "").toLowerCase().includes(input));
  });
  $("#catalog-count").textContent="2026 版 · "+rows.length+" 项匹配";
  const size=22, pages=Math.max(1, Math.ceil(rows.length/size));
  const body=rows.slice(state.catalogPage*size,(state.catalogPage+1)*size).map(([code,name]) =>
    "<tr><td>"+escape(name)+"</td><td>"+escape(data.groups[code.slice(0,4)] || "—")+"</td><td>"+
    escape(data.disciplines[code.slice(0,2)])+"</td><td>"+escape(code)+"</td></tr>").join("");
  $("#catalog-results").innerHTML=body?'<div class="table-wrap"><table><thead><tr><th>标准专业名</th><th>专业类</th><th>学科门类</th><th>专业代码</th></tr></thead><tbody>'+
    body+'</tbody></table></div><div class="pager"><button class="button" data-turn="-1" '+(state.catalogPage===0?"disabled":"")+
    '>上一页</button><span>'+(state.catalogPage+1)+' / '+pages+'</span><button class="button" data-turn="1" '+
    (state.catalogPage+1>=pages?"disabled":"")+'>下一页</button></div>':'<div class="empty">没有匹配的专业。</div>';
}
function details(id) {
  const row=candidates().find(item=>item.id===id);
  if(!row) return;
  $("#detail-title").textContent=row.id+" · "+(statusText[row.eligibility] || row.eligibility || "未分类");
  const show = (item,parent="")=>'<div class="evidence"><h3>'+escape((parent?parent+" / ":"")+(fields[item.field]||item.field))+
    pill(item.status)+'</h3><p>'+escape(item.reason || "")+'</p>'+
    (item.evidence || []).slice(0,4).map(x=>'<blockquote>'+escape((x.source || "").toUpperCase()+" "+x.quote)+'</blockquote>').join("")+'</div>';
  $("#detail-body").innerHTML=(row.hard_filters || []).map(x=>show(x)+(x.children || []).map(y=>show(y,fields[x.field])).join("")).join("")+
    (row.ai_project_or_tool_mentions || []).map(x=>'<div class="evidence"><h3>AI 项目或工具片段</h3><blockquote>'+escape(x.quote)+'</blockquote></div>').join("")+
    (row.reason?'<p class="subtitle">复核原因 · '+escape(row.reason)+'</p>':'')+
    (typeof dispositionControl==="function"?dispositionControl(id):"");
  $("#detail").showModal();
}
const corePages = {overview, candidates:candidatePage, tasks, catalog};
function navigate(page, updateUrl=true) {
  if(!Object.hasOwn(labels,page)) page="tasks";
  if(updateUrl && location.hash !== "#"+page) history.pushState(null,"","#"+page);
  state.page=page;
  state.message="";
  render();
}
function render() {
  const pageRenderer=corePages[state.page] || (typeof operationPages==="undefined"?null:operationPages[state.page]);
  let pageContent;
  try {
    if(!pageRenderer) throw new Error("页面模块未加载，请刷新工作台。");
    pageContent=pageRenderer();
  } catch(error) {
    console.error("Workbench page:",state.page,error);
    pageContent=title(labels[state.page],"")+'<p class="danger" role="alert">'+escape(error.message)+'</p>';
  }
  $("#crumb").textContent=labels[state.page];
  document.querySelectorAll("[data-page]").forEach(button=>{
    const selected=button.dataset.page===state.page;
    button.classList.toggle("active",selected);
    if(selected) button.setAttribute("aria-current","page"); else button.removeAttribute("aria-current");
  });
  content.innerHTML=(state.message?'<p class="danger">'+escape(state.message)+'</p>':'')+
    pageContent;
  if(state.page==="catalog" && $("#catalog-results")) listCatalog();
}
document.addEventListener("click", event=>{
  const button=event.target.closest("button");
  if(!button) return;
  const page=button.dataset.page || button.dataset.go;
  if(page) navigate(page);
  if(button.dataset.id) details(button.dataset.id);
  if(button.dataset.turn) {state.catalogPage+=Number(button.dataset.turn);listCatalog();}
  if(button.dataset.confirmPlan) changeSnapshot("confirm");
  if(button.dataset.editRequest) ($("#core-lines") || $("#request"))?.focus();
  if(button.dataset.replaceRequest) {
    const input=$("#request");
    const before=button.dataset.replaceRequest;
    const index=input?.value.indexOf(before) ?? -1;
    if(index>=0 && input.value.indexOf(before,index+before.length)===-1) {
      input.setRangeText(button.dataset.replacement,index,index+before.length,"select");
      state.request=input.value;
      scheduleTaskParse(true);
    }
    input?.focus();
  }
  if(button.dataset.editPlan && !state.plan?.changing && typeof beginConditionEdit==="function") {beginConditionEdit();updatePlanResult();}
  if(Object.hasOwn(button.dataset,"useExample")) {
    state.request=workspace.example_request || "本科及以上，计算机类专业，人力资源经验不超过一年";
    state.coreLines="";
    state.bonusLines="";
    $("#request").value=state.request;
    if($("#core-lines"))$("#core-lines").value="";
    if($("#bonus-lines"))$("#bonus-lines").value="";
    scheduleTaskParse(true);
    (state.job?$("#request"):$("#job")).focus();
  }
  if(typeof handleEditorClick==="function")handleEditorClick(button.dataset);
});
$("#close-detail").addEventListener("click",()=>$("#detail").close());
content.addEventListener("change",event=>{
  if(event.target.id==="report") {state.report=Number(event.target.value);render();}
  if(event.target.id==="status") {state.status=event.target.value;render();}
  if(event.target.id==="discipline") {state.discipline=event.target.value;state.catalogPage=0;listCatalog();}
});
content.addEventListener("input",event=>{
  if(event.target.id==="query"){state.query=event.target.value;state.catalogPage=0;listCatalog();}
  if(typeof syncEditor==="function")syncEditor(event);
  if(event.target.id==="request") {
    state.request=event.target.value;
    state.rewriteCount=0;
    state.rewriteNote="";
    scheduleTaskParse(!event.isComposing);
  }
});
content.addEventListener("compositionend",event=>{
  if(event.target.id==="request") {
    state.request=event.target.value;
    scheduleTaskParse(true);
  }
});
content.addEventListener("change",event=>{
  if(typeof syncEditor==="function")syncEditor(event);
  if(event.target.hasAttribute("data-boss-major")) {
    const target=event.target;
    const values=[...document.querySelectorAll("[data-boss-major]:checked")].map(input=>input.value);
    if(values.length>5) {
      target.checked=false;
      $("#boss-major-selection").textContent="最多选择 5 项。已选："+values.filter(value=>value!==target.value).join("、");
      return;
    }
    if(values.length)state.bossPageFilters["专业"]=values.join(",");
    else delete state.bossPageFilters["专业"];
    $("#boss-major-count").textContent=values.length?"已选 "+values.length+" / 5":"不限";
    $("#boss-major-selection").textContent=values.join("、");
    scheduleTaskParse(true);
  }
  const bossFilter=event.target.dataset.bossPageFilter;
  if(bossFilter) {
    if(event.target.value)state.bossPageFilters[bossFilter]=event.target.value;
    else delete state.bossPageFilters[bossFilter];
    if(bossFilter==="院校") {
      const row=document.querySelector(".boss-first-degree");
      const eligible=event.target.value==="公办本科";
      if(row)row.hidden=!eligible;
      const panel=document.querySelector(".boss-page-filters");
      if(panel)panel.open=true;
      state.bossFilterOpen=true;
      if(!eligible)delete state.bossPageFilters["只看第一学历"];
      const box=document.querySelector("[data-boss-first-degree]");
      if(box && !eligible)box.checked=false;
    }
    scheduleTaskParse(event.target.reportValidity());
  }
  if(event.target.hasAttribute("data-boss-first-degree")) {
    if(event.target.checked)state.bossPageFilters["只看第一学历"]="是";
    else delete state.bossPageFilters["只看第一学历"];
    scheduleTaskParse(true);
  }
  if(event.target.id==="job") {
    state.job=event.target.value;
    const slot=document.querySelector("#job-policy-slot");
    if(slot)slot.innerHTML=jobPolicyPanel();
    scheduleTaskParse(true);
  }
});
let parseTimer;
const taskStorageKey="workbench-task-v1";
function loadTaskState() {
  try {return JSON.parse(sessionStorage.getItem(taskStorageKey)) || {};} catch {return {};}
}
const restoredTask=loadTaskState();
let parseVersion=Number.isSafeInteger(restoredTask.version) && restoredTask.version>=0?restoredTask.version:0;
const parseTab=/^[a-f0-9]{32}$/.test(restoredTask.tab || "")?restoredTask.tab:crypto.randomUUID().replaceAll("-","");
state.job=typeof restoredTask.job==="string"?restoredTask.job:"";
state.request=typeof restoredTask.request==="string"?restoredTask.request:"";
state.coreLines=typeof restoredTask.coreLines==="string"?restoredTask.coreLines:"";
state.bonusLines=typeof restoredTask.bonusLines==="string"?restoredTask.bonusLines:"";
state.previewRun=typeof restoredTask.run==="string"?restoredTask.run:"";
try{const savedAuto=sessionStorage.getItem("workbench-auto-greet");if(savedAuto!==null)state.autoGreet=savedAuto==="1";}catch{}
state.bossPageFilters=restoredTask.bossPageFilters && typeof restoredTask.bossPageFilters==="object"?restoredTask.bossPageFilters:{};
let parseController;
const planHeaders={"Content-Type":"application/json","X-Workbench":"local-ui"};
function combinedRequest() {
  const lines=(text,prefix)=>String(text || "").split("\n").map(item=>item.trim()).filter(Boolean).map(item=>prefix+item);
  return [state.request.trim(), ...lines(state.coreLines,""), ...lines(state.bonusLines,"优先考虑：")]
    .filter(Boolean).join("\n");
}
function persistTask() {
  try {sessionStorage.setItem(taskStorageKey,JSON.stringify({tab:parseTab,version:parseVersion,job:state.job,
    request:state.request,coreLines:state.coreLines,bonusLines:state.bonusLines,run:state.previewRun,snapshot:state.plan?.snapshot?.plan_id,bossPageFilters:state.bossPageFilters}));} catch {}
}
async function restoreSnapshot() {
  if(!/^[a-f0-9]{32}$/.test(restoredTask.snapshot || ""))return;
  const version=parseVersion;
  try {
    const saved=await api("/api/plans/"+restoredTask.snapshot);
    if(version!==parseVersion || saved.job_id!==state.job || saved.source_text!==combinedRequest())return;
    state.plan=saved;
  } catch(error) {
    if(version!==parseVersion)return;
    state.plan={error:error.message+" 可用原文重新解析。"};
    persistTask();
  }
  updatePlanResult();
}
function updatePlanResult() {
  if($("#plan-result")) $("#plan-result").innerHTML=planResult();
  if(typeof workspace!=="undefined") {
    const parse=$("#parse-button");
    if(parse) {
      parse.disabled=!state.job || Boolean(state.plan?.pending);
      parse.textContent=state.plan?.pending?"正在解析…":state.plan?.task?"重新解析":"解析招聘要求";
    }
    if($("#task-steps"))$("#task-steps").innerHTML=taskSteps();
  }
}
function scheduleTaskParse(ready) {
  clearTimeout(parseTimer);
  const invalidate=Boolean(state.plan);
  ++parseVersion;
  parseController?.abort();
  state.plan=null;
  state.previewRun="";
  state.editPlan=false;
  state.forceRule=false;
  if(invalidate) api("/api/plans/invalidate",{method:"POST",headers:planHeaders,
    body:JSON.stringify({tab:parseTab,generation:parseVersion})}).catch(()=>{});
  updatePlanResult();
  if($("#task-run"))$("#task-run").innerHTML="";
  persistTask();
  if(ready && state.job && combinedRequest().trim()) parseTimer=setTimeout(parseTask,650);
}
async function runRewrite(lines, auto) {
  const items=(lines || []).filter(Boolean);
  if(!items.length) return;
  if(auto && (state.rewriteCount || 0)>=1) return;
  state.rewriteCount=(state.rewriteCount || 0)+1;
  try {
    const data=await post("/api/rewrite",{lines:items});
    const applied=[];
    for(const item of data?.rewrites || []) {
      const original=item.original, rewritten=item.rewritten;
      if(!original || !rewritten || rewritten===original) continue;
      let hit=false;
      for(const key of ["coreLines","bonusLines","request"]) {
        if(typeof state[key]==="string" && state[key].includes(original)) {
          state[key]=state[key].replace(original,rewritten);
          hit=true;
        }
      }
      if(hit) applied.push(original+" → "+rewritten);
    }
    if(!applied.length) {
      state.rewriteNote="自动改写未生效：这些条件已经足够具体，或模型没能给出更好的写法。";
      updatePlanResult();
      return;
    }
    if($("#core-lines"))$("#core-lines").value=state.coreLines;
    if($("#bonus-lines"))$("#bonus-lines").value=state.bonusLines;
    if($("#request"))$("#request").value=state.request;
    state.rewriteNote="已自动改写 "+applied.length+" 条："+applied.join("；");
    persistTask();
    scheduleTaskParse(true);
  } catch(error) {
    state.rewriteNote="自动改写失败："+error.message;
    updatePlanResult();
  }
}
function maybeAutoRewrite(result) {
  const refs=(result?.task?.reference_conditions || []).filter(Boolean);
  if(refs.length) runRewrite(refs, true);
}
async function parseTask() {
  clearTimeout(parseTimer);
  const job=state.job, request=combinedRequest();
  const activeRun=state.previewRun && typeof workspace!=="undefined" &&
    workspace.runs.find(run=>run.id===state.previewRun);
  if(!job || !request.trim() || state.plan?.pending || state.previewSubmitting ||
      ["queued","running"].includes(activeRun?.status)) return;
  if($("#plan") && !$("#plan").reportValidity())return;
  const version=++parseVersion;
  if(typeof AbortController==="function")parseController=new AbortController();
  state.editPlan=false;
  state.plan={pending:true};
  persistTask();
  updatePlanResult();
  let result;
  try{result=await api("/api/plan",{method:"POST",headers:planHeaders,
    body:JSON.stringify({request,job_id:job,tab:parseTab,generation:version,boss_page_filters:state.bossPageFilters,auto_greet:state.autoGreet!==false,force_rule:state.forceRule===true,
    core_lines:String(state.coreLines||"").split("\n").map(item=>item.trim()).filter(Boolean),
    bonus_lines:String(state.bonusLines||"").split("\n").map(item=>item.trim()).filter(Boolean)}),signal:parseController?.signal});}
  catch(error){result={error:error.message};}
  if(version!==parseVersion || state.job!==job || combinedRequest()!==request)return;
  state.plan=result;
  if(result.snapshot) state.previewRun="";
  maybeAutoRewrite(result);
  persistTask();
  updatePlanResult();
  if(typeof updateTaskStatus==="function")updateTaskStatus();
}
async function changeSnapshot(action, extras={}) {
  const original=state.plan;
  const snapshot=original?.snapshot;
  if(!snapshot || original.changing) return;
  original.changing=true;
  delete original.actionError;
  updatePlanResult();
  try {
    const updated=await api("/api/plans/"+snapshot.plan_id+"/"+action,{method:"POST",headers:planHeaders,
      body:JSON.stringify({task_hash:snapshot.task_hash,...extras})});
    if(state.plan!==original) return;
    state.plan=updated;
    state.editPlan=false;
    if(action==="revise")state.previewRun="";
    persistTask();
  } catch(error) {
    if(state.plan===original) original.actionError=error.message;
  } finally {
    original.changing=false;
  }
  updatePlanResult();
}
content.addEventListener("submit",event=>{
  if(event.target.id==="revise-plan"){
    event.preventDefault();
    try {
      changeSnapshot("revise",editedConditions(event.target));
    } catch(error) {
      state.plan.actionError=error instanceof SyntaxError?"高级条件格式有误，请检查后重试。":error.message;
      updatePlanResult();
    }
    return;
  }
  if(event.target.id!=="plan")return;
  event.preventDefault();
  parseTask();
});
$("#import").addEventListener("change",async event=>{
  try {
    const file=event.target.files[0];
    if(!file)return;
    const data=JSON.parse((await file.text()).replace(/^\uFEFF/,""));
    const rows=data.rows || data.anonymous_rows;
    if(!Array.isArray(rows) || !rows.every(row=>row && typeof row.id==="string"))throw new Error("请选择包含匿名候选人 rows 的筛选报告");
    state.reports.unshift({stamp:file.name,data});state.report=0;state.status="";state.page="candidates";state.message="";
  } catch(error) {state.message=error.message;}
  event.target.value="";
  render();
});
window.addEventListener("hashchange",()=>navigate(location.hash.slice(1),false));
document.addEventListener("DOMContentLoaded",()=>{
  navigate(location.hash.slice(1),false);
  restoreSnapshot();
},{once:true});
Promise.allSettled([api("/api/reports"),api("/api/catalog")]).then(([reports,catalogData])=>{
  if(reports.status==="fulfilled") state.reports.push(...reports.value);
  else state.message=reports.reason.message;
  if(catalogData.status==="fulfilled")state.catalog=catalogData.value;
  render();
});

content.addEventListener("input",event=>{
  if(event.target.id!=="boss-major-search")return;
  const keyword=event.target.value.trim().toLowerCase();
  document.querySelectorAll(".boss-major-options label").forEach(label=>{
    label.hidden=Boolean(keyword) && !(label.textContent || "").toLowerCase().includes(keyword);
  });
  document.querySelectorAll(".boss-major-editor details").forEach(group=>{
    if(group.classList.contains("boss-major-editor"))return;
    const visible=[...group.querySelectorAll(".boss-major-options label")].some(label=>!label.hidden);
    group.hidden=!visible;
    if(keyword && visible)group.open=true;
  });
});

content.addEventListener("toggle",event=>{
  if(event.target.classList?.contains("boss-page-filters")) state.bossFilterOpen=event.target.open;
},true);

content.addEventListener("click",async event=>{
  const button=event.target.closest("[data-save-policy]");
  if(!button)return;
  if(!state.job){state.message="请先选择岗位";render();return;}
  const mode=document.querySelector("#policy-mode")?.value || "strict_all";
  const body={job_id:state.job, decision_mode:mode,
    min_core_percent:Number(document.querySelector("#min-core")?.value ?? 100) || 0,
    min_bonus_count:Number(document.querySelector("#min-bonus")?.value ?? 0) || 0};
  button.disabled=true;
  try {
    await post("/api/job-policy",body);
    await refreshWorkspace(true);
    const status=document.querySelector("#policy-status");
    if(status)status.textContent="已保存，正在按新规则重新解析";
    scheduleTaskParse(true);
  } catch(error) {
    state.message=error.message;
    render();
  } finally {
    button.disabled=false;
  }
});

content.addEventListener("input",event=>{
  if(event.target.id!=="core-lines" && event.target.id!=="bonus-lines")return;
  if(event.target.id==="core-lines")state.coreLines=event.target.value;
  else state.bonusLines=event.target.value;
  state.rewriteCount=0;
  persistTask();
  scheduleTaskParse(!event.isComposing);
});

content.addEventListener("change",event=>{
  if(event.target.id!=="policy-mode")return;
  const mode=event.target.value;
  const hint=document.querySelector("#policy-hint");
  if(hint)hint.textContent=policyHint(mode);
  const row=document.querySelector("#policy-weighted");
  if(row)row.hidden=mode!=="weighted";
  if(mode==="weighted") {
    const core=document.querySelector("#min-core"), bonus=document.querySelector("#min-bonus");
    if(core && Number(core.value)===100)core.value="60";
    if(bonus && Number(bonus.value)===0)bonus.value="1";
  }
});

content.addEventListener("click",event=>{
  if(event.target.closest("[data-rewrite-refs]")) {
    state.rewriteCount=0;
    state.rewriteNote="";
    runRewrite(state.plan?.task?.reference_conditions || [], false);
    return;
  }
  if(!event.target.closest("[data-force-rule]"))return;
  state.forceRule=true;
  parseTask();
});
