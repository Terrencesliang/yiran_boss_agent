const reviewState={run:"",data:null,error:"",filters:{},request:0,detailRequest:0,detail:null,study:null,caseKey:"",sample:null,studyRequest:0};
const reviewLabels={PENDING:"待复核",IN_REVIEW:"复核中",CONFIRMED_MATCH:"人工确认符合",
  CONFIRMED_NON_MATCH:"人工确认不符合",NEED_MORE_EVIDENCE:"需要补充证据",UNRESOLVED_CONFLICT:"证据冲突尚未解决"};
Object.assign(labels,{review:"候选人",differences:"规则差异"});
Object.assign(statusText,{CONFLICT:"证据冲突"});
const reviewReasons={
  MAJOR_NOT_PROVIDED:"未取得专业证据",MAJOR_UNMAPPED:"专业无法可靠映射",MAJOR_SCOPE_UNCLEAR:"专业范围待确认",
  MAJOR_OUT_OF_SCOPE:"专业不在要求范围内",MAJOR_SOURCE_CONFLICT:"同一教育记录的专业证据冲突",
  OFFICIAL_MAJOR_FAILED:"正式专业条件未通过",OFFICIAL_HR_FAILED:"正式 HR 条件未通过",
  HR_EXPERIENCE_UNKNOWN_UPPER_BOUND:"HR 时长上界未知",HR_EXPERIENCE_MISSING_EVIDENCE:"缺少 HR 经历证据",
  HR_EXPERIENCE_BELOW_MIN:"HR 经历不足",HR_EXPERIENCE_EXCEEDS_MAX:"HR 经历超过上限",
  HR_EXPERIENCE_DATE_CONFLICT:"HR 任职日期冲突",HR_RELEVANCE_UNCERTAIN:"HR 职责或区间无法确认",
  OCR_UNRELIABLE:"OCR 可靠性不足",CAPTURE_INCOMPLETE:"采集不完整",CAPTURE_IDENTITY_MISMATCH:"详情身份核验失败",
  EVIDENCE_REFERENCE_INVALID:"引文无法核验",EVIDENCE_CONFLICT:"原文证据冲突",
  PREFERENCE_UNVERIFIED:"优先条件未提供证据",PREFERENCE_FAILED:"优先条件不符合",
};
const eligibleReviewRuns=()=>workspace.runs.filter(run=>run.action==="preview" && run.status==="completed" && run.plan_id);
const reviewOptions=(items,selected)=>items.map(([key,name])=>'<option value="'+escape(key)+'"'+
  (selected===key?' selected':'')+'>'+escape(name)+'</option>').join("");
const reviewFlatten=items=>items.flatMap(item=>[item,...reviewFlatten(item.children || [])]);
const reviewRunUrl=()=>"/api/runs/"+encodeURIComponent(reviewState.run);
const reviewDisplay=value=>value==null?"未知":Array.isArray(value)?value.join("、"):String(value);
const reviewLabelFor=code=>reviewReasons[code] || code;
const candidateLabel=row=>{
  const number=row.candidate_number ?? (reviewState.data?.rows.findIndex(item=>item.candidate_id===row.candidate_id) ?? -1)+1;
  return row.candidate_name || (number?"候选人 "+String(number).padStart(2,"0"):"候选人");
};
const educationLabels={education_level:"学历",education_mode:"学习形式",school:"学校",major:"专业",major_group:"专业类别"};
function displayedSchool(values) {
  const schools=[...new Set(values.map(value=>value.trim().replace(/^教育经历\s*[:：]?\s*/,"")).filter(Boolean))];
  const activity=/^(?:在校期间|积极完成学校内布置的作业学习|社团经历|校内活动|校园活动)/;
  const names=schools.filter(name=>!activity.test(name));
  const corroborated=names.filter(name=>!names.some(other=>other!==name && other.includes(name)) &&
    schools.some(text=>activity.test(text) && text.endsWith(name)));
  if(corroborated.length===1) {
    const name=corroborated[0];
    const nearVariant=other=>name.startsWith(other) ||
      (name.length===other.length && [...name].filter((char,index)=>char!==other[index]).length<=2);
    if(names.every(nearVariant))return name;
  }
  return names.length>1?"学校名称不一致（"+names.length+" 个提取值）":names[0];
}
function educationSummary(row) {
  if(!row.education_summary && row.official_status!=="CAPTURE_FAILED")
    return '<span class="muted">'+(row.educationSummaryFailed?"暂时无法读取教育信息":"正在读取教育信息…")+'</span>';
  const summary=row.education_summary || {};
  const value=(field,ambiguous)=>(summary[field]?.length>1?ambiguous:summary[field]?.[0]) || "";
  const major=value("major","专业未确认") || value("major_group","专业类别未确认");
  const headline=[value("education_level","学历未确认"),value("education_mode","学习形式未确认"),major].filter(Boolean).join(" · ");
  const school=displayedSchool(summary.school || []);
  if(!headline && !school)return '<span class="muted">未提取到学历或专业</span>';
  return '<div class="review-education-summary">'+[headline,school].filter(Boolean).map(text=>
    '<div title="'+escape(text)+'">'+escape(text)+'</div>').join("")+'</div>';
}
async function loadMissingEducation(data,url,request) {
  const missing=data.rows.filter(row=>!row.education_summary && row.official_status!=="CAPTURE_FAILED");
  for(let start=0;start<missing.length && request===reviewState.request;start+=6) {
    await Promise.all(missing.slice(start,start+6).map(async row=>{
      try {
        const result=await api(url+"/"+encodeURIComponent(row.candidate_id));
        const facts=result.candidate.candidate_evidence?.facts || [];
        row.education_summary=result.candidate.education_summary || Object.fromEntries(Object.keys(educationLabels).map(field=>[field,
          [...new Set(facts.filter(fact=>fact.field===field && fact.raw_value!=null).map(fact=>String(fact.raw_value)))]
        ]));
      } catch {
        row.educationSummaryFailed=true;
      }
      if(request!==reviewState.request)return;
      const cell=document.querySelector('[data-review-education="'+CSS.escape(row.candidate_id)+'"]');
      if(cell)cell.innerHTML=educationSummary(row);
    }));
  }
}
function reviewWorkspaceReady() {
  if(!["review","differences"].includes(state.page) || reviewState.run) return;
  const eligible=eligibleReviewRuns();
  let previous;
  try{previous=sessionStorage.getItem("workbench-review-run");}catch{}
  const run=eligible.find(item=>item.id===previous) || eligible[0];
  if(run) openReviewRun(run.id);
}
async function openReviewRun(id) {
  const request=++reviewState.request;
  reviewState.run=id;
  try{sessionStorage.setItem("workbench-review-run",id);}catch{}
  reviewState.data=null;
  reviewState.error="";
  render();
  if(!id)return;
  const url=reviewRunUrl()+"/candidates";
  try {
    const data=await api(url);
    if(request===reviewState.request) {
      reviewState.data=data;
      if(!data.shadow_engine) {
        delete reviewState.filters.shadow;
        delete reviewState.filters.different;
      }
    }
  } catch(error) {
    if(request===reviewState.request) reviewState.error=error.message;
  }
  if(request===reviewState.request && ["review","differences"].includes(state.page)) render();
  if(request===reviewState.request && reviewState.data)
    await loadMissingEducation(reviewState.data,url,request);
}
function selectedReviewRows() {
  const {filters,data}=reviewState;
  const filtered=(data?.rows || []).filter(row=>{
    const codes=row.reason_codes || [];
    const issue={
      major_missing: codes.includes("MAJOR_NOT_PROVIDED"),
      major_mismatch: codes.includes("MAJOR_OUT_OF_SCOPE") || codes.includes("OFFICIAL_MAJOR_FAILED"),
      hr_low: codes.includes("HR_EXPERIENCE_BELOW_MIN"),
      hr_failed: codes.includes("OFFICIAL_HR_FAILED"),
      hr_unknown: codes.includes("HR_EXPERIENCE_UNKNOWN_UPPER_BOUND"),
      conflict: row.shadow?.status==="CONFLICT" || codes.includes("EVIDENCE_CONFLICT"),
      capture_failed: row.official_status==="CAPTURE_FAILED",
    };
    const query=(filters.query || "").trim().toLowerCase();
    return (!query || [candidateLabel(row),row.candidate_id].some(value=>value.toLowerCase().includes(query))) &&
      (!filters.official || row.official_status===filters.official) &&
      (!filters.shadow || (row.shadow?.status || "unavailable")===filters.shadow) &&
      (!filters.different || (filters.different==="yes" ? row.different : row.shadow && !row.different)) &&
      (!filters.issue || issue[filters.issue]) &&
      (!filters.review || (filters.review==="pending" ? !["CONFIRMED_MATCH","CONFIRMED_NON_MATCH"].includes(row.review_status)
        : ["CONFIRMED_MATCH","CONFIRMED_NON_MATCH"].includes(row.review_status)));
  });
  if(filters.sort==="id")return filtered.sort((a,b)=>candidateLabel(a).localeCompare(candidateLabel(b),"zh-CN",{numeric:true}));
  if(!filters.sort)return filtered.sort((a,b)=>(b.match_percent ?? -1)-(a.match_percent ?? -1) ||
    candidateLabel(a).localeCompare(candidateLabel(b),"zh-CN",{numeric:true}));
  if(filters.sort==="score")return filtered.sort((a,b)=>(b.official_score ?? -Infinity)-(a.official_score ?? -Infinity));
  return filtered;
}
const conditionNames={education_mode:"是否全日制",hr_experience_months:"人力资源工作时长",
  major_group:"专业",OR:"任选一项的条件",AND:"组合条件"};
function screeningReasons(row) {
  if(row.official_status==="CAPTURE_FAILED") {
    return [row.capture_reason==="candidate_id_required"?
      "未能确认候选人身份，未完成简历采集":"未能完成简历采集"];
  }
  const reasons=[];
  if(row.official_status==="UNKNOWN" && row.review_reason==="capture_incomplete")
    reasons.push("简历采集不完整");
  if(row.official_status==="UNKNOWN" && row.review_reason==="hard_condition_citation_unverified")
    reasons.push("筛选依据缺少可核对的简历原文");
  function addConditions(items) {
    for(const item of items) {
      if(item.status!==row.official_status || item.status==="PASS") continue;
      if(item.field==="AND" && item.children?.length) {
        addConditions(item.children);
        continue;
      }
      const majorGroup=item.field==="OR" && item.children?.length &&
        item.children.every(child=>["major","major_group"].includes(child.field));
      const toolGroup=item.field==="OR" && item.children?.length &&
        item.children.every(child=>child.field==="hands_on_tools");
      const name=majorGroup?"专业":toolGroup?"实操工具":conditionNames[item.field] || fields[item.field] || "招聘条件";
      if(item.status==="FAIL") reasons.push(name+"不符合要求");
      if(item.status==="UNKNOWN") reasons.push(
        item.field==="hr_experience_months" && row.hr_review_hint ? row.hr_review_hint :
        name==="专业" && row.reason_codes?.includes("MAJOR_NOT_PROVIDED")?"未取得专业证据":"未提供"+name+"的证据");
    }
  }
  addConditions(row.official_criteria || []);
  if(row.strict_all_conditions && row.hard_filter_status==="PASS" && row.official_status!=="PASS")
    reasons.push("硬性要求已满足，历史口径将未命中的优先条件计为未通过");
  if(!reasons.length && row.official_status==="UNKNOWN") reasons.push("报告未记录具体原因，请查看简历依据");
  return [...new Set(reasons)];
}
function pendingSummary(rows) {
  const counts=new Map();
  for(const row of rows) {
    if(row.official_status!=="UNKNOWN") continue;
    for(const reason of screeningReasons(row)) counts.set(reason,(counts.get(reason) || 0)+1);
  }
  if(!counts.size) return "";
  const common=[...counts].sort((a,b)=>b[1]-a[1]).slice(0,3);
  return '<details class="review-why"><summary>不符合的常见原因（同一人可能有多项）</summary>'+
    '<p class="muted">'+common.map(([reason,count])=>escape(reason)+"（"+count+" 人）").join(" · ")+'</p></details>';
}
function reviewTable(rows, differences) {
  const columns=differences?["候选人","正式资格","影子资格","差异","原因","人工复核"]:
    ["候选人","学历 / 专业","匹配度","结论","主要原因","人工复核"];
  if(!rows.length) return '<p class="empty">'+(differences?"此运行没有待验证的引擎差异。":"没有符合筛选条件的记录。")+'</p>';
  return '<div class="table-wrap"><table class="review-table"><thead><tr>'+columns.map(name=>'<th>'+name+'</th>').join("")+
    '</tr></thead><tbody>'+rows.map(row=>{
      const shadow=row.shadow?.status;
      const cells=['<button data-review-candidate="'+escape(row.candidate_id)+'" title="'+escape(row.candidate_id)+'">'+escape(candidateLabel(row))+'</button>'];
      if(!differences) cells.push(educationSummary(row));
      if(!differences) cells.push(matchCell(row));
      cells.push(pill(row.official_status));
      if(differences) cells.push(shadow?pill(shadow):"未运行",
        row.different?(row.compatibility_different?"待验证 · 资格差异":"待验证 · 状态语义"):(shadow?"一致":"—"));
      const reasons=differences?(row.reason_codes || []).filter(code=>Object.hasOwn(reviewReasons,code)).map(reviewLabelFor):
        screeningReasons(row);
      cells.push(escape(reasons.join("；") || (row.official_status==="PASS"?"全部条件符合":"—")),
        '<span class="review-state">'+escape(reviewLabels[row.review_status])+'</span>');
      const reasonsText=reasons.join("；");
      return '<tr data-row-candidate="'+escape(row.candidate_id)+'">'+cells.map((value,index)=>'<td'+
        (!differences && index===1?' data-review-education="'+escape(row.candidate_id)+'"':'')+
        (!differences && index===4?' class="review-reasons" title="'+escape(reasonsText)+'"':'')+'>'+value+'</td>').join("")+'</tr>';
    }).join("")+'</tbody></table></div>';
}
function matchPercent(row,compact) {
  if(!Number.isFinite(row.match_percent))return compact?"—":"";
  const percent=Math.round(row.match_percent);
  const detail=Number.isFinite(row.matched_conditions)&&Number.isFinite(row.total_conditions)?
    '<span class="match-detail">'+row.matched_conditions+" / "+row.total_conditions+'</span>':"";
  return '<span class="match-percent'+(percent>=100?" is-full":"")+'">'+
    (compact?"":"匹配度 ")+percent+'%'+detail+'</span>';
}
function matchCell(row) {
  if(!Number.isFinite(row.match_percent))return '<span class="muted">—</span>';
  const percent=Math.round(row.match_percent);
  return '<div class="match-cell"><span class="match-percent'+(percent>=100?" is-full":"")+'">'+percent+'%</span>'+
    (Number.isFinite(row.matched_conditions)?'<small class="match-detail">'+row.matched_conditions+' / '+row.total_conditions+'</small>':'')+
    '<progress class="mini-progress" max="100" value="'+percent+'" aria-label="匹配度 '+percent+'%"></progress></div>';
}
function reviewPage(differences=false) {
  const data=reviewState.data;
  const runs=eligibleReviewRuns();
  const picker='<label class="run-picker"><span>筛选批次</span><select id="review-run">'+reviewOptions(
    [["","选择运行"],...runs.map(run=>[run.id,run.job+" · "+run.id])],reviewState.run)+'</select></label>';
  const study=differences && workspace.review_study_enabled?'<button class="button" data-study-open>打开合成盲标</button>':'';
  const header='<div class="page-head"><div><h1>'+escape(differences?"规则差异":"候选人")+'</h1><p class="muted">'+
    escape(differences?"逐项核对新旧规则差异。影子结果为辅助分析。":"查看本次筛选结果、原文依据和人工复核记录。")+'</p></div>'+
    '<div class="page-actions">'+picker+study+
    (!differences && data?.shadow_engine?'<button class="button" data-go="differences">查看规则差异</button>':'')+'</div></div>';
  if(!data) return header+'<section class="panel"><p class="empty">'+
    escape(reviewState.error || (reviewState.run?"正在读取运行报告…":"请选择已完成的确认快照运行。"))+'</p></section>'+studyPanel();
  const unknownCount=data.summary.UNKNOWN || 0;
  const stats=[["符合",data.summary.PASS,"pass",""],["不符合",(data.summary.FAIL || 0)+unknownCount,"fail",
    unknownCount?"其中 "+unknownCount+" 人未提供证据":""],["采集失败",data.summary.CAPTURE_FAILED,"",""],
    ["已复核",(data.reviewed_count ?? 0)+" / "+(data.checked ?? 0),"",""]];
  if(differences) stats.unshift(["新旧差异",data.different_count,"",""]);
  const statusOptions=[["PASS","符合"],["FAIL","不符合"],["UNKNOWN","未提供证据"],["CAPTURE_FAILED","采集失败"]];
  const filter=(field,label,options)=>'<label>'+label+' <select data-review-filter="'+field+'">'+
    reviewOptions([["","全部"],...options],reviewState.filters[field])+'</select></label>';
  const filters=differences?"":'<div class="review-filters">'+
    '<label>搜索候选人 <input id="review-search" type="search" placeholder="输入姓名或候选人编号" value="'+escape(reviewState.filters.query || "")+'"></label>'+
    filter("official","结论",statusOptions)+
    filter("review","复核状态",[["pending","尚未完成"],["completed","已完成"]])+
    '</div><details class="review-advanced"><summary>更多筛选</summary><div class="review-filters">'+
    filter("sort","排序",[["id","按姓名或编号"],["score","偏好评分从高到低"]])+
    filter("issue","证据问题",[["major_missing","专业缺失"],["major_mismatch","专业不匹配"],["hr_low","人力资源经历不足"],
      ["hr_failed","人力资源经历不符合"],["hr_unknown","人力资源工作时长不明"],["conflict","证据冲突"],["capture_failed","采集失败"]])+
    (data.shadow_engine?filter("shadow","影子资格",[...statusOptions,["CONFLICT","证据冲突"],["unavailable","未运行"]])+
      filter("different","引擎差异",[["yes","有差异"],["no","一致"]]):"")+'</div></details>';
  const metadata=entries=>'<dl class="review-meta">'+entries.map(([key,value])=>'<div><dt>'+key+'</dt><dd>'+escape(value)+'</dd></div>').join("")+'</dl>';
  return header+'<section class="panel"><div class="panel-top"><h2>本次筛选</h2></div>'+
    metadata([["招聘岗位",data.job],["筛选时间",productDate(data.screened_at)],
    ["检查人数",data.checked],["人工复核",(data.reviewed_count ?? 0)+" / "+(data.checked ?? 0)]])+
    (data.strict_all_conditions?'<p class="review-legacy-note">这是历史批次，按当时的 100% 匹配口径记录。新批次以硬性要求判定，优先条件用于排序。</p>':'')+
    '<details class="review-advanced"><summary>高级详情</summary>'+metadata([["正式引擎",data.official_engine],
      ["辅助分析版本",data.shadow_engine || "未运行"],["确认记录",data.snapshot.plan_id],["任务哈希",data.snapshot.task_hash]])+
    '</details></section><div class="stats">'+stats.map(([key,value,tone,note])=>'<div class="stat '+tone+'"><span>'+key+
      '</span><strong>'+escape(value ?? 0)+'</strong>'+(note?'<small class="stat-note">'+escape(note)+'</small>':'')+'</div>').join("")+'</div><section class="panel">'+
    (differences?"":pendingSummary(data.rows))+filters+
    '<div id="review-table-results">'+reviewTable(differences?data.rows.filter(row=>row.different):selectedReviewRows(),differences)+
    '</div></section>'+studyPanel();
}

function refLinks(ids, evidence) {
  return (ids || []).map(id=>evidence[id]?'<button type="button" class="button citation" data-review-ref="'+escape(id)+'" data-source="'+escape(evidence[id].source_type)+'">'+
    (evidence[id].source_type==="OCR"?"查看图片识别原文":"查看页面原文")+'</button>':'<span>暂未取得可核验的原文证据</span>').join(" ");
}
function evidenceCards(evidence,batch) {
  const items=Object.entries(evidence);
  return items.length?'<div class="review-citations">'+items.map(([id,ref])=>
    '<article class="evidence" id="review-ref-'+escape(id)+'" tabindex="-1"><h3>'+escape(ref.source_type)+
    '</h3><small>'+escape([ref.source_id,batch,ref.quality,Number.isInteger(ref.start)?'偏移 '+ref.start+'–'+ref.end:null].filter(Boolean).join(" · "))+
    '</small>'+(ref.quote?'<blockquote>'+escape(ref.quote)+'</blockquote>':'<p>证据不可用</p>')+'</article>').join("")+'</div>':
    '<p class="subtitle">暂未取得可核验的原文证据</p>';
}
function reviewCriteria(items,evidence) {
  return items.map(item=>{
    const caption=escape(fields[item.field] || item.field);
    const links=refLinks(item.evidence_ids,evidence);
    const details='<p>预期 '+escape(reviewDisplay(item.expected))+'</p>'+
      (item.requirement?'<blockquote>'+escape(item.requirement)+'</blockquote>':'')+
      '<p>'+escape(item.reason || "")+'</p>'+
      ((item.observed!=null || item.reason_codes?.length)?'<details><summary>高级判定详情</summary><small>'+
      escape([...(item.reason_codes || []),item.rule_version].filter(Boolean).join(" · "))+'</small>'+
      (item.observed!=null?'<pre class="response">'+escape(JSON.stringify(item.observed,null,2))+'</pre>':'')+'</details>':'')+
      (links?'<p>'+links+'</p>':'<p class="subtitle">暂未取得可核验的原文证据</p>');
    if(item.children?.length) return '<details class="evidence"><summary>'+caption+' '+pill(item.status)+'</summary>'+
      details+reviewCriteria(item.children,evidence)+'</details>';
    return '<div class="evidence"><h3>'+caption+' '+pill(item.status)+'</h3>'+details+'</div>';
  }).join("") || '<p class="subtitle">没有可用的逐项判定。</p>';
}
const educationFields={...educationLabels,degree:"学位",education_start_year:"入学年份",graduation_year:"毕业年份"};
const educationStatus={confirmed:"已确认",pending:"待确认",missing:"未提取",conflict:"证据冲突"};
const educationReasons={capture_incomplete:"采集不完整",parse_failed:"模型解析失败",not_found:"未发现原文依据"};
function educationReviewForm(row,record,refs,events) {
  return '<form class="form" data-education-review="'+escape(record.record_id)+'">'+
    '<label>字段<select name="field">'+reviewOptions(Object.entries(educationFields))+'</select></label>'+
    '<label>操作<select name="action">'+reviewOptions([["confirm","确认已有值"],["correct","修正"],["revoke","撤销人工复核"]])+'</select></label>'+
    '<label>核对后的值<input name="value" maxlength="60"></label>'+
    '<label>原文依据<select name="evidence" multiple>'+reviewOptions(refs.map(id=>[id,row.evidence[id].quote.slice(0,110)]))+'</select></label>'+
    '<label>理由<textarea name="reason" required maxlength="1500"></textarea></label>'+
    '<div class="form-actions"><button type="submit" class="button is-primary">保存字段复核</button><p role="status"></p></div></form>'+
    (events.length?'<ul>'+events.map(event=>'<li>'+escape(educationFields[event.field]+" · "+
      ({confirm:"确认",correct:"修正",revoke:"撤销"}[event.action])+" · "+(event.value || "")+" "+event.reason+
      " · "+productDate(event.reviewed_at))+'</li>').join("")+'</ul>':"");
}
function educationRecordCards(row,history) {
  const data=row.candidate_evidence;
  const facts=new Map((data.facts || []).map(fact=>[fact.fact_id,fact]));
  const groups=new Map();
  for(const record of data.education_records || []) {
    const entries=record.fact_ids.map(id=>facts.get(id)).filter(Boolean);
    const refs=[...new Set(entries.flatMap(fact=>fact.evidence_ids))].filter(id=>row.evidence[id]);
    const events=history.filter(event=>event.record_id===record.record_id && event.evidence_revision===row.evidence_revision);
    const values={};
    for(const [key,label] of Object.entries(educationFields)) {
      const field=record.fields?.[key];
      const alternatives=entries.filter(fact=>fact.field===key);
      const latest=events.findLast(event=>event.field===key);
      const human=latest && latest.action!=="revoke"?latest:null;
      const status=human?"confirmed":field?.status || (alternatives.length?"pending":"missing");
      const value=human?.value || field?.value || [...new Set(alternatives.map(fact=>fact.raw_value))].join(" / ");
      if(!value)continue;
      const ids=(human?human.evidence_ids:alternatives.flatMap(fact=>fact.evidence_ids)) || [];
      values[key]={label,value,status,human,source:ids.find(id=>row.evidence[id]),
        meta:[...new Set(alternatives.map(fact=>[fact.normalized_value!==fact.raw_value?fact.normalized_value:null,
          fact.major?.mapping_method?"目录匹配":null].filter(Boolean).join(" · ")).filter(Boolean))],
        reason:field?.reason && educationReasons[field.reason]};
    }
    const key=[values.major?.value,values.education_start_year?.value,values.graduation_year?.value]
      .filter(Boolean).join("|") || record.record_id;
    if(!groups.has(key))groups.set(key,[]);
    groups.get(key).push({record,values,events,refs});
  }
  return [...groups.values()].map(group=>{
    const merged={};
    for(const [key,label] of Object.entries(educationFields)) {
      const items=group.map(item=>item.values[key]).filter(Boolean);
      if(!items.length)continue;
      const distinct=[...new Set(items.map(item=>item.value))];
      merged[key]={label,value:distinct.join(" / "),conflict:distinct.length>1,
        source:items.map(item=>item.source).find(Boolean),
        meta:[...new Set(items.flatMap(item=>item.meta))],
        reason:items.map(item=>item.reason).find(Boolean),
        human:items.some(item=>item.human)};
    }
    const rows=Object.entries(merged).map(([key,item])=>
      '<div class="edu-row"><span class="edu-label">'+escape(item.label)+'</span>'+
      '<span class="edu-value">'+escape(item.value)+'</span>'+
      '<span class="edu-side">'+
        (item.conflict?'<span class="edu-tag is-warn">原文不一致</span>':
         item.human?'<span class="edu-tag is-human">人工确认</span>':'')+
        (item.source?'<button type="button" class="edu-source" data-review-ref="'+escape(item.source)+'">原文</button>':'')+
      '</span>'+
      (item.meta.length || item.reason?'<span class="edu-note">'+escape([...item.meta,item.reason].filter(Boolean).join(" · "))+'</span>':'')+
      '</div>').join("");
    const missing=Object.entries(educationFields).filter(([key])=>!merged[key]).map(([,label])=>label);
    const school=group[0].values.school?.value || (group.find(item=>item.values.school) || {}).values?.school?.value
      || group[0].record.school || "教育经历";
    const forms=group.map((item,index)=>'<details class="edu-review"><summary>复核教育字段'+
      (group.length>1?' · 记录 '+(index+1)+(item.values.school?.value?"（"+escape(item.values.school.value)+"）":""):"")+
      '</summary>'+educationReviewForm(row,item.record,item.refs,item.events)+'</details>').join("");
    return '<article class="edu-record"><h4>'+escape(school)+'</h4>'+
      '<div class="edu-list">'+rows+'</div>'+
      (missing.length?'<details class="edu-missing"><summary>'+missing.length+' 项未提取</summary><p>'+escape(missing.join("、"))+'</p></details>':'')+
      (group.length>1?'<details class="edu-sources"><summary>这段经历有 '+group.length+' 条原文记录</summary><p class="muted">同一段教育经历出现多个来源，字段不一致处在上面标为「原文不一致」，可逐条复核。</p>'+forms+'</details>':forms)+
      '</article>';
  }).join("");
}
function educationPanel(data,evidence,row,history) {
  if(data.education_pipeline) {
    const cards=educationRecordCards(row,history);
    const status=data.education_pipeline.status;
    return '<h3>教育经历</h3>'+
      (cards && !(data.facts || []).some(fact=>["major","major_group"].includes(fact.field))?
        '<p class="muted">未取得专业证据</p>':"")+
      (cards || '<p class="muted">'+escape(status==="capture_incomplete"?"简历采集不完整，未识别到教育经历。":
        status==="parse_failed"?"模型解析失败，未识别到教育经历。":"未识别到教育经历。")+'</p>');
  }
  const facts=data.facts || [];
  const majors=facts.filter(fact=>fact.major);
  if(!facts.length) return '<h3>学历与专业</h3><p class="muted">'+
    (row.official_status==="CAPTURE_FAILED"?"简历采集失败，未取得教育信息。":"本次未提取到教育信息。")+'</p>';
  const labels={...educationLabels,graduation_year:"毕业年份",graduate_status:"应届身份"};
  const summary=Object.entries(labels).map(([field,label])=>{
    const items=facts.filter(fact=>fact.field===field);
    const rawValues=[...new Set(items.map(fact=>fact.raw_value))];
    const values=field==="school"?displayedSchool(rawValues):rawValues.join("、");
    return values?'<div><dt>'+label+'</dt><dd>'+escape(values)+
      (items.some(fact=>fact.extraction_status==="CONFLICT")?' '+pill("CONFLICT"):'')+'</dd></div>':"";
  }).join("");
  return '<h3>学历与专业</h3><dl class="candidate-facts">'+summary+'</dl>'+
    (!majors.length?'<p class="muted">未取得专业证据</p>':'')+
    '<details><summary>查看教育经历的原文依据</summary>'+
    facts.map(fact=>{
      const major=fact.major, scopes=(major?.business_scopes || []).map(scope=>scope.name+" "+scope.version+" "+scope.rationale);
      return '<div class="evidence"><strong>'+escape(fields[fact.field] || (fact.field==="school"?"学校":fact.field))+
        '</strong><p>'+escape(fact.raw_value)+'</p>'+
        (major?'<details><summary>专业识别依据</summary><p>'+escape([major.normalized_major || "未确认标准专业名称",
          major.major_code,major.major_category,...scopes].filter(Boolean).join(" · "))+'</p></details>':'')+
        (fact.extraction_status==="CONFLICT"?pill("CONFLICT"):'')+refLinks(fact.evidence_ids,evidence)+'</div>';
    }).join("")+'</details>'+
    (data.education_records?.length?'<details><summary>按教育经历逐项复核</summary>'+educationRecordCards(row,history)+'</details>':"");
}
function resumeFragments(row) {
  const structured=[...(row.candidate_evidence.facts || []),...(row.candidate_evidence.projects || [])];
  const usages=[
    ["工作经历",row.candidate_evidence.experiences || []],
    ["结构化提取",structured],
    ["筛选规则",reviewFlatten(row.official_criteria.concat(row.preference_criteria || [],row.shadow?.criteria || []))],
  ].map(([label,items])=>[label,new Set(items.flatMap(item=>item.evidence_ids || []))]);
  const byLocation=new Map();
  for(const [id,ref] of Object.entries(row.evidence)) {
    const key=JSON.stringify([ref.source_type,ref.start,ref.end,ref.quote]);
    if(!byLocation.has(key)) byLocation.set(key,{...ref,ids:[]});
    byLocation.get(key).ids.push(id);
  }
  return [...byLocation.values()].sort((a,b)=>a.start-b.start).map((ref,index)=>({
    ...ref,number:index+1,uses:usages.filter(([,ids])=>ref.ids.some(id=>ids.has(id))).map(([label])=>label),
  }));
}
const sourceLabel=source=>source==="OCR"?"OCR 文本":source==="DOM"?"页面文本":source;
const offsets=ref=>"偏移 "+ref.start+"–"+ref.end;
function switches(group,options) {
  return '<div class="proof-switch" role="group" aria-label="'+group+'">'+options.map(([key,label],i)=>
    '<button type="button" data-proof-mode="'+key+'" aria-pressed="'+(i===0)+'">'+label+'</button>').join("")+'</div>';
}
function experienceItem(group,fragments) {
  const {row,parts}=group;
  const sources=[...new Set(parts.map(ref=>ref.source_type))];
  const period=[row.start_date,row.end_date].map(value=>value || "未提取").join(" 至 ");
  return '<article class="proof-employment"><header><strong>'+(group.pending?"任职信息（待补充）":"任职信息")+'</strong>'+
    (sources.length?'<span class="proof-chip">来源 · '+escape(sources.map(sourceLabel).join(" / "))+'</span>':'')+'</header>'+
    '<dl><div><dt>公司</dt><dd>'+escape(row.company || "未提取")+'</dd></div><div><dt>岗位</dt><dd>'+
    escape(row.position || "未提取")+'</dd></div><div><dt>时间</dt><dd>'+
    escape(row.start_date || row.end_date?period:"未提取")+'</dd></div></dl>'+
    (row.responsibilities?.length && !group.pending?'<h4>提取的职责</h4><ul class="proof-duties">'+
      row.responsibilities.map(text=>'<li>'+escape(text)+'</li>').join("")+'</ul>':'')+
    (parts.length?'<details class="proof-disclosure" open><summary>工作相关原文（'+parts.length+' 段）</summary>'+
      parts.map(ref=>'<div class="work-quote"><p>'+escape(ref.quote)+'</p>'+
        refLinks([ref.ids[0]],fragments)+'</div>').join("")+'</details>':'')+'</article>';
}
function timelinePanel(data,evidence,fragments) {
  const timeline=data.hr_timeline;
  const rows=[...(data.experiences || [])].sort((a,b)=>String(b.start_date || "").localeCompare(String(a.start_date || "")));
  const ids=new Set(rows.flatMap(row=>row.evidence_ids));
  const parts=fragments.filter(ref=>ref.ids.some(id=>ids.has(id)));
  const groups=[];
  const pendingRows=[];
  for(const row of rows) {
    if(!row.company && !row.position && !row.start_date && !row.end_date) pendingRows.push(row);
    else groups.push({row,parts:parts.filter(ref=>ref.ids.some(id=>row.evidence_ids.includes(id)))});
  }
  if(pendingRows.length) {
    const pendingIds=new Set(pendingRows.flatMap(row=>row.evidence_ids));
    groups.push({row:{},pending:true,parts:parts.filter(ref=>ref.ids.some(id=>pendingIds.has(id)))});
  }
  return '<div class="proof-panel-head"><h3>工作经历</h3><details class="proof-rule"><summary>提取规则说明</summary>'+
    '<p>从页面和 OCR 文字提取公司、岗位、任职日期和职责。缺少任职信息的原文归在待补充组，每段仍保留出处。</p></details></div>'+
    (rows.length?'<div class="proof-duration"><div><span>人力资源相关工作时长</span><strong>'+
      (timeline?.confirmed_hr_days>0?escape(timeline.confirmed_hr_days)+" 天":"无法确认")+'</strong></div><p>'+
      (timeline?.confirmed_hr_days>0?"已累计有日期依据的相关经历"+(timeline.has_unknown_upper_bound?"，总时长无法确认。":"。"):
        "本次未核实到人力资源工作时长"+(timeline?.has_unknown_upper_bound?"，总时长无法确认。":"。"))+
      (parts.length && !(timeline?.confirmed_hr_days>0)?"已找到 "+parts.length+" 段工作相关证据。":"")+'</p></div>'+
    '<div class="proof-panel-head"><h4>工作经历 <small>（'+parts.length+' 段证据）</small></h4>'+
    switches("工作经历查看方式",[["merged","按任职归组 "+groups.length],["work-evidence","按证据查看 "+parts.length]])+'</div>'+
    '<div data-proof-pane="merged">'+groups.map(group=>experienceItem(group,evidence)).join("")+'</div>'+
    '<div data-proof-pane="work-evidence" hidden>'+parts.map(ref=>'<article class="proof-fragment"><p>'+escape(ref.quote)+'</p><small>'+
      escape(sourceLabel(ref.source_type)+" · "+offsets(ref))+'</small>'+refLinks([ref.ids[0]],evidence)+'</article>').join("")+'</div>':
      '<p class="muted">未取得工作经历正文。</p>');
}
const expectedLabels={full_time:"全日制",part_time:"非全日制",secondary:"中专",bachelor:"本科",associate:"大专",master:"硕士",doctorate:"博士"};
const extraFieldNames={job_related_requirement:"岗位相关要求",job_related_preference:"加分项",
  resume_keywords:"关键词",industry_experience:"行业经验",experience_months:"相关经验"};
function cleanValue(value) {
  return String(value ?? "").replace(/^[:：]\s*/,"").replace(/\s+/g," ").trim();
}
function candidateRequirement(item,rule) {
  if(item.children?.length) {
    const joiner=item.field==="OR"?" 或 ":"，且 ";
    return item.children.map((child,index)=>candidateRequirement(child,rule?.conditions?.[index])).join(joiner);
  }
  const rawField=String(item.field || "");
  const [base,detail]=rawField.split(":");
  const baseName=extraFieldNames[base] || fields[base] || conditionNames[base] || base;
  const name=detail?baseName+"（"+detail+"）":baseName;
  const value=item.expected ?? rule?.value;
  const labels=Array.isArray(value)?value:[value];
  const expected=labels.map(value=>expectedLabels[cleanValue(value)] || reviewDisplay(cleanValue(value)));
  if(base==="experience_months")return name+"："+expected.join("、")+" 个月";
  if(["job_related_requirement","job_related_preference","resume_keywords"].includes(base))
    return name+"："+expected.join("、");
  if(rule?.operator==="between" && item.field==="hr_experience_months" && value?.[0]===0)
    return "人力资源工作时长不超过 "+value[1]+" 个月";
  const operator={gt:"超过 ",gte:"至少 ",lte:"最多 "}[rule?.operator] || "";
  const separator=rule?.operator==="between"?" 至 ":["one_of","has_any"].includes(rule?.operator)?" 或 ":"、";
  return name+" "+operator+expected.join(separator);
}
function candidateChecks(items,rules=[]) {
  const conditions=items.map((item,index)=>({item,rule:rules[index]}));
  conditions.sort((a,b)=>(a.item.status==="PASS")-(b.item.status==="PASS"));
  return conditions.length?'<ul class="candidate-checks">'+conditions.map(({item,rule})=>
    '<li><span>'+escape(candidateRequirement(item,rule))+'</span>'+pill(item.status)+'</li>').join("")+'</ul>':"";
}
function resumeFragment(ref,withTarget) {
  return '<article class="proof-fragment"'+(withTarget?' id="review-ref-'+escape(ref.ids[0])+'" tabindex="-1"':'')+
    '><span class="proof-number">'+ref.number+'</span><div><p>'+escape(ref.quote)+'</p><small>'+
    escape(sourceLabel(ref.source_type)+" · "+offsets(ref))+'</small></div>'+
    (ref.uses.length?'<span class="proof-chip">用于 '+escape(ref.uses.join("、"))+'</span>':'')+'</article>';
}
const resumeSections=/^(工作经历|项目经历|项目经验|实习经历|教育经历|教育背景|专业技能|技能特长|自我评价|个人优势|求职意向|期望职位|基本信息|最近关注|工作经历)/;
function formatResumeHtml(text) {
  const lines=String(text || "").split("\n").map(line=>line.trim()).filter(Boolean);
  const html=[];
  let bullets=[];
  const flush=()=>{
    if(bullets.length) {
      html.push('<ul>'+bullets.map(item=>'<li>'+escape(item)+'</li>').join("")+'</ul>');
      bullets=[];
    }
  };
  for(const line of lines) {
    const section=line.match(resumeSections);
    if(section) {
      flush();
      const rest=line.slice(section[1].length).replace(/^[:：\s]+/,"");
      html.push('<h4>'+escape(section[1])+'</h4>');
      if(rest)html.push('<p>'+escape(rest)+'</p>');
      continue;
    }
    const bullet=line.replace(/^[·•\-*]\s*/,"").replace(/^\d+[.、)]\s*/,"").replace(/^（\d+）/,"");
    if(bullet!==line) { bullets.push(bullet); continue; }
    if(line.length>60 && /[；;]/.test(line)) {
      flush();
      const parts=line.split(/[；;]/).map(item=>item.trim()).filter(Boolean);
      html.push('<ul>'+parts.map(item=>'<li>'+escape(item)+'</li>').join("")+'</ul>');
      continue;
    }
    flush();
    html.push('<p>'+escape(line)+'</p>');
  }
  flush();
  return html.join("");
}
function cleanResumeText(text) {
  return String(text || "").split("\n").map(line=>line.trim())
    .filter(line=>line && !/^(收藏|转发|举报|不合适|打招呼|更多|关闭|返回|分享|复制|下载|查看|收起|展开|在线简历|附件简历|沟通|交换微信|求简历)$/.test(line))
    .join("\n");
}
function ocrLooksBad(text) {
  const value=String(text || "");
  const cjk=(value.match(/[\u4e00-\u9fff]/g) || []).length;
  return value.length>40 && cjk/Math.max(1,value.length)<0.2;
}
function resumePanel(row,fragments) {
  const sources=Object.entries(row.resume_sources || {}).filter(([,text])=>text.trim());
  const groups=[...new Set(["OCR","DOM",...fragments.map(ref=>ref.source_type)])];
  return '<div class="proof-panel-head"><h3>简历内容</h3><div class="proof-actions">'+
    (sources.length || fragments.length?'<button class="button" type="button" data-resume-open>查看原文</button>':'')+
    (sources.length?'<button class="button" type="button" data-resume-download>下载简历文本</button>':'')+'</div></div>'+
    (sources.length?
      (row.candidate_evidence.capture_status==="partial"?'<p class="muted">本次采集不完整，以下为已取得的正文。</p>':'')+
      '<p class="muted">页面摘要来自 BOSS 详情页可见内容（已去掉按钮文字）；附件识别来自简历图片，质量以标注为准。</p>'+
      sources.map(([source,text],index)=>{
        const kind=source.toUpperCase();
        const display=kind==="DOM"?cleanResumeText(text):text;
        const bad=kind==="OCR" && ocrLooksBad(display);
        return '<details class="proof-disclosure"'+(index===0?' open':'')+'><summary>'+
          escape(sourceLabel(kind))+(bad?' <small class="warn">识别质量较差，仅供参考</small>':"")+
          ' <small>（'+String(display).length+' 字）</small></summary><div class="proof-text">'+
          formatResumeHtml(display.replace(/█+/g,"［已隐藏］"))+'</div></details>';
      }).join(""):
      '<p class="muted">'+(fragments.length?"此批次仅保存了用于筛选和复核的原文片段。":"此批次没有可显示的简历正文。")+'</p>')+
    (fragments.length?'<details class="proof-disclosure" id="candidate-section-evidence"'+(sources.length?'':' open')+
      '><summary>查看已保存的简历片段 <small>（'+fragments.length+' 段 · 已去重）</small></summary>'+
      switches("简历片段排序",[["source","按来源分组"],["offset","按位置顺序"]])+
      '<div class="review-citations proof-source-groups" data-proof-pane="source">'+groups.map(source=>{
        const refs=fragments.filter(ref=>ref.source_type===source);
        return '<details class="proof-disclosure" open><summary>'+escape(sourceLabel(source))+' <small>（'+refs.length+
          ' 段）</small></summary>'+(refs.length?refs.map(ref=>resumeFragment(ref,true)).join(""):'<p class="muted">本批次无此来源的引用片段。</p>')+'</details>';
      }).join("")+'</div><div data-proof-pane="offset" hidden>'+fragments.map(ref=>resumeFragment(ref,false)).join("")+
      '</div></details>':'');
}
function reasonLines(row) {
  const items=[...(row.official_criteria || []),...(row.preference_criteria || [])];
  if(!items.length)return "";
  const buckets={PASS:[],FAIL:[],UNKNOWN:[]};
  const lines=items.map(item=>{
    const status=item.status || "UNKNOWN";
    const icon=status==="PASS"?"✓":status==="FAIL"?"✗":"?";
    const label=candidateRequirement(item);
    const short=label.split("：").pop();
    (buckets[status] || buckets.UNKNOWN).push(short);
    const quotes=(item.evidence_ids || []).map(id=>row.evidence?.[id]?.quote).filter(Boolean)
      .map(text=>String(text).replace(/\s+/g," ").slice(0,160));
    let detail=String(item.reason || "").trim();
    const generic=!detail || /三值|求值|子条件|按子条件/.test(detail);
    if(generic)detail=status==="PASS"?"简历原文可支持该条件":"简历中未找到可核对的相关描述";
    if(status==="PASS" && quotes.length)detail=detail+"　原文："+quotes[0];
    const tail=status==="PASS"?"，满足要求。":status==="FAIL"?"，不符合。":"，待人工确认。";
    return '<li class="reason-line is-'+status.toLowerCase()+'"><span class="reason-icon">'+icon+
      '</span><b>'+escape(label)+'</b><span>'+escape(detail+tail)+'</span></li>';
  }).join("");
  const brief=[
    buckets.PASS.length?"已满足："+buckets.PASS.slice(0,6).join("、"):"",
    buckets.FAIL.length?"不符合："+buckets.FAIL.slice(0,4).join("、"):"",
    buckets.UNKNOWN.length?"待核实："+buckets.UNKNOWN.slice(0,6).join("、"):""
  ].filter(Boolean).join("；");
  return '<section class="candidate-card candidate-reasons"><h3>筛选依据</h3>'+
    (brief?'<p class="reason-brief">'+escape(brief)+'</p>':'')+
    '<p class="muted">逐条对照简历原文：勾选表示有原文支持，叉号表示明确不符，问号表示简历里找不到可核对的内容。</p>'+
    '<ul class="reason-list">'+lines+'</ul></section>';
}
function policyText(row) {
  const mode=row.decision_mode;
  if(mode==="strict_core")return "本次判定：核心要求需全部满足，加分项只影响排序。";
  if(mode==="weighted")return "本次判定：核心满足 ≥"+(row.min_core_percent ?? 100)+"% 且加分项 ≥"+(row.min_bonus_count ?? 0)+" 项。";
  if(mode==="strict_all")return "本次判定：全部条件（核心+加分）满足才算符合。";
  return "此运行按旧口径判定（优先条件仅计分）；匹配度为按现有条件补算。";
}
function verdictText(row) {
  if(row.strict_all_conditions && row.hard_filter_status==="PASS" && row.official_status!=="PASS")
    return "硬性要求已满足。该历史批次将优先条件也计入了通过门槛。";
  if(row.official_status!=="PASS")return "请查看下方岗位要求。";
  const pending=(row.preference_criteria || []).filter(item=>item.status!=="PASS");
  if(row.strict_all_conditions)return pending.length?"必选条件已满足，但优先条件仍未全部符合。":"全部条件均符合。";
  return pending.length?"满足本次筛选的硬性要求，部分优先条件未命中。":"满足本次筛选的硬性要求。";
}
function renderReviewDetail() {
  const current=reviewState.detail;
  if(!current) return;
  const {candidate:row,history,task,education_history:educationHistory=[]}=current, evidence=row.evidence, raw=row.candidate_evidence;
  const latest=history[0];
  const fragments=resumeFragments(row);
  $("#review-detail").dataset.candidateId=row.candidate_id;
  $("#review-title").textContent=candidateLabel(row);
  $("#review-position").textContent=task.job || "简历与筛选结果";
  const refs=Object.entries(evidence).map(([key,value])=>[key,value.source_type+" · "+(value.quote || "证据不可用").slice(0,65)]);
  const criteria=reviewFlatten(row.official_criteria.concat(row.preference_criteria || [],row.shadow?.criteria || []))
    .map(item=>[item.criterion_id,candidateRequirement(item)+(item.criterion_id.startsWith("official")?"":"（辅助分析）")]);
  $("#review-body").innerHTML='<section class="candidate-result" data-official-status="'+escape(row.official_status)+'" data-shadow-status="'+
    escape(row.shadow?.status || "")+'"><div class="candidate-heading"><h3>筛选结果</h3>'+pill(row.official_status)+matchPercent(row)+'</div>'+
    '<p>'+escape(screeningReasons(row).join("；") || verdictText(row))+'</p>'+
    '<div class="candidate-actions"><button class="button is-primary" type="button" data-candidate-section="resume">查看简历</button>'+
    '<button class="button" type="button" data-candidate-section="review">人工复核</button></div></section>'+
    reasonLines(row)+
    '<div class="candidate-layout"><div class="candidate-main">'+
    '<section class="candidate-card candidate-education">'+educationPanel(raw,evidence,row,educationHistory)+'</section>'+
    '</div><div class="candidate-side">'+
    (row.official_criteria.length || row.preference_criteria?.length?
      '<section class="candidate-card candidate-match"><h3>岗位要求与匹配情况</h3>'+
      '<p class="muted candidate-rule">'+escape(policyText(row))+'</p>'+
      candidateChecks(row.official_criteria,task.hard_filters)+
      (row.preference_criteria?.length?'<h4>加分项</h4>'+candidateChecks(row.preference_criteria):'')+'</section>':'')+
    '</div></div><div class="candidate-proof-grid"><section class="candidate-card candidate-experience">'+timelinePanel(raw,evidence,fragments)+
    '</section><section class="candidate-card" id="candidate-section-resume" tabindex="-1">'+resumePanel(row,fragments)+'</section></div>'+
    '<div class="candidate-proof-grid">'+
    '<section class="candidate-card" id="candidate-section-review" tabindex="-1"><h3>人工复核</h3>'+
    '<p class="muted candidate-review-status">'+escape(reviewLabels[latest?.review_status || "PENDING"])+'</p>'+
    '<form id="candidate-review-form" class="form"><label>结论<select name="status">'+
    reviewOptions(Object.entries(reviewLabels),latest?.review_status || "PENDING")+
    '</select></label><label>理由<textarea name="reason" required maxlength="1500"></textarea></label>'+
    '<details><summary>关联岗位要求与简历依据</summary><label>岗位要求<select name="criteria" multiple>'+reviewOptions(criteria)+'</select></label>'+
    '<label>简历依据<select name="evidence" multiple>'+reviewOptions(refs)+'</select></label></details>'+
    '<div class="form-actions"><button class="button is-primary" type="submit">保存人工复核</button><p id="review-feedback" role="status"></p></div></form>'+
    (history.length?history.map(record=>'<article class="evidence"><h3>版本 '+escape(record.version)+' · '+
      escape(reviewLabels[record.review_status])+'</h3><small>'+escape(record.reviewed_at+' · '+record.reviewer_id.slice(0,12))+
      '</small><p>'+escape(record.review_reason)+'</p>'+refLinks(record.evidence_ids,evidence)+'</article>').join(""):
      '<p class="subtitle">暂无复核记录</p>')+'</section>'+
    '<details class="candidate-card" id="candidate-section-technical"><summary>查看详细判定记录</summary>'+
    '<h4>筛选条件</h4>'+reviewCriteria(row.official_criteria,evidence)+
    (row.preference_criteria?.length?'<h4>加分项</h4>'+reviewCriteria(row.preference_criteria,evidence):'')+
    (row.shadow?'<h4>辅助分析 '+pill(row.shadow.status)+'</h4>'+reviewCriteria(row.shadow.criteria || [],evidence):'')+
    (row.capture_reason?'<p>'+escape(row.capture_reason)+'</p>':'')+
    '<details><summary>运行记录与工作时长计算</summary><p>'+escape(row.candidate_id)+'</p><pre class="response">'+
    escape(JSON.stringify({任务:task,人力资源工作时长:raw.hr_timeline},null,2))+'</pre></details></details></div>';
}
async function showReviewCandidate(id) {
  const request=++reviewState.detailRequest;
  const run=reviewState.run;
  const result=await api(reviewRunUrl()+"/candidates/"+encodeURIComponent(id));
  if(request!==reviewState.detailRequest || run!==reviewState.run) return;
  reviewState.detail=result;
  renderReviewDetail();
  if(!$("#review-detail").open) {
    $("#review-detail").showModal();
    $("#review-detail").scrollTop=0;
  }
}

function studyPanel() {
  if(!reviewState.study || state.page!=="differences")return "";
  const {study,sample,caseKey}=reviewState;
  const current=sample?.comparison;
  const controls='<div class="panel-top"><div><h2>合成独立盲标</h2><p>四态差异 '+escape(study.differences)+
    ' · 兼容资格差异 '+escape(study.compatibility_differences)+'</p></div><label>样本 <select id="study-case">'+
    reviewOptions([["","请选择"],...study.cases.map(item=>[item.key,item.case_id+(item.submitted?" · 已提交":"")])],caseKey)+
    '</select></label></div>';
  if(!sample)return '<section class="panel">'+controls+'<p class="empty">请选择合成样本。</p></section>';
  const refs=Object.entries(sample.evidence).map(([key,ref])=>[key,ref.source_type+" · "+ref.quote.slice(0,52)]);
  const affected=new Set(sample.disagreements || []);
  const leaves=reviewFlatten(sample.criteria).filter(item=>!item.children?.length);
  const form=(adjudication)=>'<form id="'+(adjudication?"study-adjudication":"study-annotation")+'" class="form">'+
    leaves.filter(item=>!adjudication || affected.has(item.criterion_id)).map(item=>
      '<fieldset data-study-row="'+escape(item.criterion_id)+'"><legend>'+escape(item.criterion_id+" · "+(fields[item.field] || item.field))+
      '</legend><p>要求 '+escape(item.operator+" "+reviewDisplay(item.value))+'</p><label>状态<select name="status" required>'+
      reviewOptions([["","选择"],...["PASS","FAIL","UNKNOWN","CONFLICT"].map(key=>[key,statusText[key]])])+
      '</select></label><label>依据<textarea name="reason" required maxlength="1500"></textarea></label>'+
      '<label>引用证据<select name="refs" multiple>'+reviewOptions(refs)+'</select></label></fieldset>').join("")+
    (adjudication?'<label>裁决授权密钥<input type="password" required name="key" autocomplete="off"></label>':'')+
    '<div class="form-actions"><button class="button is-primary" type="submit">'+(adjudication?"保存人工裁决":"封存盲标并查看引擎结果")+'</button></div></form>';
  const decisions=record=>'<article class="evidence"><strong>版本 '+escape(record.version)+' · '+escape(record.reviewer_id.slice(0,12))+
    '</strong><small> '+escape(record.reviewed_at)+'</small>'+record.decisions.map(item=>'<p>'+
      escape(item.criterion_id)+' '+pill(item.status)+' '+escape(item.reason)+' '+refLinks(item.evidence_ids,sample.evidence)+'</p>').join("")+'</article>';
  const comparison=current?'<h3>引擎结果</h3><p>旧资格 '+pill(current.legacy_status)+' · 影子资格 '+pill(current.shadow.status)+
    '</p><p class="subtitle">未获独立人工裁决前，差异均待验证。</p>'+
    reviewCriteria(current.shadow.criteria,current.shadow.evidence)+
    '<h3>独立标注</h3>'+sample.annotations.map(decisions).join("")+
    (affected.size?'<h3>分歧与裁决</h3><p>存在分歧的条件 '+escape([...affected].join("、"))+'</p>'+
      (sample.adjudications || []).map(decisions).join("")+form(true):'<p class="subtitle">当前无独立标注分歧。</p>'):
    '<p class="subtitle">提交前只显示任务条件和合成原文。</p>'+form(false);
  const evidence={...sample.evidence,...(current?.shadow.evidence || {})};
  return '<section class="panel">'+controls+'<div class="review-study-body">'+
    (sample.capture_note?'<p class="danger">'+escape(sample.capture_note)+'</p>':'')+
    '<details open><summary>合成原文</summary>'+evidenceCards(evidence,"离线模拟")+'</details>'+
    comparison+'<p id="study-feedback" role="status"></p></div></section>';
}
async function openStudy() {
  reviewState.study=await api("/api/review-study");
  render();
}
async function openStudyCase(key) {
  const serial=++reviewState.studyRequest;
  reviewState.caseKey=key;
  reviewState.sample=null;
  render();
  if(!key)return;
  const sample=await api("/api/review-study/"+encodeURIComponent(key));
  if(serial!==reviewState.studyRequest)return;
  reviewState.sample=sample;
  render();
}
operationPages.review=()=>reviewPage();
operationPages.differences=()=>reviewPage(true);

document.addEventListener("click",async event=>{
  const row=event.target.closest("tr[data-row-candidate]");
  if(row && !event.target.closest("button,a,input,select,label")) {
    try { await showReviewCandidate(row.dataset.rowCandidate); } catch(error) { state.message=error.message; render(); }
    return;
  }
  const data=event.target.closest("button")?.dataset;
  if(!data)return;
  try {
    if(data.page==="review" || data.page==="differences")reviewWorkspaceReady();
    if(data.reviewCandidate) await showReviewCandidate(data.reviewCandidate);
    if(Object.hasOwn(data,"reviewClose")) {
      ++reviewState.detailRequest;
      $("#review-detail").close();
    }
    if(data.candidateSection) {
      const element=$("#candidate-section-"+data.candidateSection);
      const fragments=$("#candidate-section-evidence");
      if(data.candidateSection==="resume" && fragments && !Object.keys(reviewState.detail.candidate.resume_sources || {}).length)
        fragments.open=true;
      element.focus({preventScroll:true});
      element.scrollIntoView({block:"start"});
    }
    if(data.proofMode) {
      const panel=event.target.closest(".candidate-card");
      const controls=event.target.closest(".proof-switch");
      for(const button of controls.querySelectorAll("button")) button.setAttribute("aria-pressed",String(button.dataset.proofMode===data.proofMode));
      for(const pane of panel.querySelectorAll("[data-proof-pane]")) pane.hidden=pane.dataset.proofPane!==data.proofMode;
    }
    if(Object.hasOwn(data,"resumeOpen")) {
      const section=$("#candidate-section-resume");
      const original=section.querySelector(".proof-disclosure");
      if(original) {
        original.open=true;
        original.scrollIntoView({block:"nearest"});
        original.querySelector("summary").focus({preventScroll:true});
      }
    }
    if(Object.hasOwn(data,"resumeDownload")) {
      const row=reviewState.detail.candidate;
      const content=Object.entries(row.resume_sources || {}).filter(([,value])=>value.trim())
        .map(([source,value])=>sourceLabel(source.toUpperCase())+"\n"+value.replace(/█+/g,"［已隐藏］")).join("\n\n");
      if(content) {
        const url=URL.createObjectURL(new Blob(["\uFEFF",content],{type:"text/plain;charset=utf-8"}));
        const link=document.createElement("a");
        link.href=url;
        link.download=candidateLabel(row).replace(/[\\/:*?"<>|]/g,"_")+"-简历.txt";
        link.click();
        setTimeout(()=>URL.revokeObjectURL(url),1000);
      }
    }
    if(data.reviewRef) {
      const inCandidate=Boolean(event.target.closest("#review-detail"));
      const ref=inCandidate?resumeFragments(reviewState.detail.candidate).find(item=>item.ids.includes(data.reviewRef)):null;
      if(ref) $('#candidate-section-resume [data-proof-mode="source"]').click();
      const matches=[...document.querySelectorAll("#review-ref-"+CSS.escape(ref?.ids[0] || data.reviewRef))];
      const element=matches.find(item=>item.closest("dialog")?.open) || matches[0];
      if(element) {
        for(let parent=element.parentElement;parent;parent=parent.parentElement) {
          if(parent.tagName==="DETAILS") parent.open=true;
        }
        element.scrollIntoView({block:"nearest"});
        element.focus({preventScroll:true});
      }
    }
    if(Object.hasOwn(data,"studyOpen"))await openStudy();
  } catch(error) {
    state.message=error.message;
    render();
  }
});
document.addEventListener("change",async event=>{
  const target=event.target;
  try{
    if(target.id==="review-run") await openReviewRun(target.value);
    if(target.dataset.reviewFilter) {
      reviewState.filters[target.dataset.reviewFilter]=target.value;
      render();
    }
    if(target.id==="study-case")await openStudyCase(target.value);
  }catch(error){
    state.message=error.message;
    render();
  }
});
document.addEventListener("input",event=>{
  if(event.target.id!=="review-search")return;
  reviewState.filters.query=event.target.value;
  $("#review-table-results").innerHTML=reviewTable(selectedReviewRows(),false);
});
document.addEventListener("submit",async event=>{
  const form=event.target;
  if(form.dataset.educationReview) {
    event.preventDefault();
    const current=reviewState.detail;
    const run=reviewState.run;
    const row=current.candidate;
    const button=form.querySelector('button[type="submit"]');
    if(button.disabled)return;
    button.disabled=true;
    const feedback=form.querySelector("[role=status]");
    try {
      await post(reviewRunUrl()+"/education-reviews",{
        candidate_id:row.candidate_id,record_id:form.dataset.educationReview,field:form.elements.field.value,
        action:form.elements.action.value,value:form.elements.value.value,reason:form.elements.reason.value,
        evidence_ids:[...form.elements.evidence.selectedOptions].map(option=>option.value),
        expected_version:current.education_history?.at(-1)?.version || 0,evidence_revision:row.evidence_revision,
      });
      if(reviewState.detail===current && reviewState.run===run && $("#review-detail").open) {
        await showReviewCandidate(row.candidate_id);
        const updated=reviewState.detail?.candidate;
        const entry=reviewState.data?.rows.find(item=>item.candidate_id===row.candidate_id);
        if(reviewState.run===run && updated?.candidate_id===row.candidate_id && entry) {
          entry.education_summary=updated.education_summary;
          const cell=document.querySelector('[data-review-education="'+CSS.escape(row.candidate_id)+'"]');
          if(cell)cell.innerHTML=educationSummary(entry);
        }
      }
    } catch(error) {
      feedback.textContent=error.message;
    } finally {
      button.disabled=false;
    }
    return;
  }
  if(!["candidate-review-form","study-annotation","study-adjudication"].includes(form.id))return;
  event.preventDefault();
  const field=form.querySelector("button[type=submit]");
  if(field.disabled)return;
  field.disabled=true;
  const previous=field.textContent;
  field.textContent="正在保存…";
  const selected=select=>[...select.selectedOptions].map(option=>option.value);
  const candidate=form.id==="candidate-review-form";
  const feedback=candidate?"#review-feedback":"#study-feedback";
  try{
    if(candidate) {
      const data=reviewState.detail;
      const id=data.candidate.candidate_id;
      const run=reviewState.run;
      await post(reviewRunUrl()+"/reviews",{candidate_id:id,expected_version:data.history[0]?.version || 0,
        evidence_revision:data.candidate.evidence_revision,review_status:form.elements.status.value,reason:form.elements.reason.value,
        condition_ids:selected(form.elements.criteria),evidence_ids:selected(form.elements.evidence)});
      if(reviewState.run===run) {
        await openReviewRun(run);
        await showReviewCandidate(id);
        $(feedback).textContent="已保存人工复核";
      }
    } else {
      const adjudicate=form.id==="study-adjudication";
      const key=reviewState.caseKey;
      const decisions=[...form.querySelectorAll("[data-study-row]")].map(row=>({criterion_id:row.dataset.studyRow,
        status:row.querySelector("[name=status]").value,reason:row.querySelector("[name=reason]").value,
        evidence_ids:selected(row.querySelector("[name=refs]"))}));
      const payload={expected_version:adjudicate?reviewState.sample.adjudications[0]?.version || 0:0,decisions};
      if(adjudicate)payload.adjudication_key=form.elements.key.value;
      await post("/api/review-study/"+encodeURIComponent(key)+"/"+(adjudicate?"adjudicate":"annotate"),payload);
      if(reviewState.caseKey===key) {
        await openStudy();
        await openStudyCase(key);
        if($(feedback))$(feedback).textContent=adjudicate?"裁决已保存":"独立标注已封存";
      }
    }
  }catch(error){
    if($(feedback))$(feedback).textContent=error.message;
  }finally{
    field.disabled=false;
    field.textContent=previous;
  }
});
