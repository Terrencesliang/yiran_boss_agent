const editorFields={
  education_level:"最低学历",major_group:"专业范围",major:"具体专业",hr_experience_months:"人力资源经验",
  experience_months:"工作经验",job_related_requirement:"必备经历",job_related_preference:"优先经历",
};
const degreeOptions={secondary:"中专",associate:"大专",bachelor:"本科",master:"硕士",doctorate:"博士"};
const listOptions=(entries,selected)=>entries.map(([value,label])=>'<option value="'+escape(value)+'"'+
  (value===selected?" selected":"")+'>'+escape(label)+'</option>').join("");
const editorCopy=value=>JSON.parse(JSON.stringify(value));

function beginConditionEdit() {
  const task=state.plan.task;
  state.editPlan={hard:editorCopy(task.hard_filters),soft:editorCopy(task.soft_preferences),reason:"",advanced:false};
}
function editorNode(path) {
  const [key,...indexes]=path.split(".");
  let siblings=state.editPlan[key];
  for(const index of indexes.slice(0,-1)) siblings=siblings[index].conditions;
  return [siblings,Number(indexes.at(-1))];
}
function conditionEditor() {
  const edit=state.editPlan;
  if(!Array.isArray(edit.hard)) return "";
  if(edit.advanced) return '<form id="revise-plan" class="condition-editor"><p>高级条件编辑 · 保存后需要重新确认。</p>'+
    '<label>硬性要求<textarea name="hard" rows="7" required>'+escape(edit.jsonHard ?? JSON.stringify(edit.hard,null,2))+'</textarea></label>'+
    '<label>优先条件<textarea name="soft" rows="7" required>'+escape(edit.jsonSoft ?? JSON.stringify(edit.soft,null,2))+'</textarea></label>'+
    editorFooter(true)+'</form>';
  const groups=[...new Set(Object.values(state.catalog?.groups || {}))];
  const select=(path,property,items,value,label)=>'<label>'+label+'<select data-editor-path="'+path+'" data-property="'+property+'">'+
    listOptions(items,value)+'</select></label>';
  const input=(path,property,value,label,type="text")=>'<label>'+label+'<input data-editor-path="'+path+
    '" data-property="'+property+'" type="'+type+'" '+(type==="number"?'min="0" max="1200" step="1" ':'')+
    'value="'+escape(value ?? "")+'" required></label>';
  const renderNode=(item,path,level)=>{
    const outer=level===0?'<button class="button subtle" type="button" data-editor-move="'+path+'">'+
      (path.startsWith("hard.")?"移到优先条件":"设为硬性要求")+'</button>':'';
    if(item.conditions) {
      const operators=item.operator==="NOT"?[["NOT","排除"]]:[["AND","全部满足（且）"],["OR","任一满足（或）"]];
      return '<fieldset class="condition-group"><legend>组合条件</legend>'+select(path,"operator",operators,item.operator,"满足关系")+
        item.conditions.map((child,i)=>renderNode(child,path+"."+i,level+1)).join("")+outer+'</fieldset>';
    }
    let controls="";
    if(item.field==="education_level") {
      controls=select(path,"operator",[["gte","至少"],["equals","仅限"]],item.operator,"学历要求")+
        select(path,"value",Object.entries(degreeOptions),item.value,"学历");
    } else if(item.field==="major_group") {
      controls=select(path,"value",[["","选择专业类"],...[...new Set([...groups,item.value])].filter(Boolean).map(value=>[value,value])],item.value,"专业类");
    } else if(item.field==="major" && item.operator==="equals") {
      controls=input(path,"value",item.value,"标准专业名称");
    } else if(["hr_experience_months","experience_months"].includes(item.field)) {
      controls=select(path,"operator",[["between","区间"],["lte","最多"],["gte","至少"],["gt","超过"],["equals","等于"]],item.operator,"年限口径");
      controls+=item.operator==="between"?input(path,"lower",item.value[0],"最少月数","number")+
        input(path,"upper",item.value[1],"最多月数","number"):input(path,"number",item.value,"月数","number");
    } else if(["job_related_requirement","job_related_preference"].includes(item.field) && item.operator==="verify") {
      controls=input(path,"value",item.value,"经历说明");
    } else {
      controls='<p>'+escape(condition(item))+' <small>可在高级编辑中调整</small></p>';
    }
    return '<div class="condition-row"><strong>'+escape(editorFields[item.field] || fields[item.field] || "条件")+
      '</strong><div class="editor-fields">'+controls+'</div>'+outer+'</div>';
  };
  const area=(key,label)=>'<section class="condition-area"><h3>'+label+'</h3>'+
    (edit[key].length?edit[key].map((item,i)=>renderNode(item,key+"."+i,0)).join(""):'<p class="subtitle">暂无条件</p>')+
    '<div class="controls"><button type="button" class="button" data-editor-add="'+key+'">添加条件</button>'+
    (edit[key].length>=2?'<button type="button" class="button" data-editor-group="'+key+'">将本组条件组合</button>':'')+'</div></section>';
  return '<form id="revise-plan" class="condition-editor">'+area("hard","硬性要求")+area("soft","优先条件")+
    (edit.addTo?'<div class="controls"><label>添加条件类型<select id="editor-add-field">'+
      listOptions(Object.entries(editorFields).filter(([key])=>key!=="job_related_preference"),"education_level")+
      '</select></label><button type="button" class="button" data-editor-insert>添加</button></div>':'')+
    editorFooter(false)+'</form>';
}
function editorFooter(advanced) {
  return '<label>修改理由<input name="reason" required maxlength="500" value="'+escape(state.editPlan.reason)+'" placeholder="例如：将最低学历调整为本科"></label>'+
    '<div class="controls"><button class="button is-primary" type="submit" '+(state.plan.changing?"disabled":"")+'>'+
    (state.plan.changing?"正在保存…":"保存条件修改")+'</button><button class="button" type="button" data-editor-cancel>取消</button>'+
    '<button class="button subtle" type="button" data-editor-advanced>'+(advanced?"返回表单":"高级编辑")+'</button></div>';
}
function syncEditor(event) {
  const target=event.target;
  const edit=state.editPlan;
  if(!edit || target.form?.id!=="revise-plan")return;
  if(target.name==="reason")edit.reason=target.value;
  if(target.name==="hard")edit.jsonHard=target.value;
  if(target.name==="soft")edit.jsonSoft=target.value;
  if(!target.dataset.editorPath)return;
  const [siblings,index]=editorNode(target.dataset.editorPath);
  const item=siblings[index], property=target.dataset.property;
  if(property==="operator") {
    if(Array.isArray(item.value) && target.value!=="between")item.value=item.value[1];
    else if(!Array.isArray(item.value) && target.value==="between")item.value=[0,item.value];
    item.operator=target.value;
    if(event.type==="change" && !item.conditions)updatePlanResult();
  } else if(property==="number")item.value=target.value===""?null:Number(target.value);
  else if(property==="lower" || property==="upper")item.value[property==="lower"?0:1]=target.value===""?null:Number(target.value);
  else item.value=target.value;
}
function handleEditorClick(data) {
  const edit=state.editPlan;
  if(!edit || state.plan.changing)return;
  if(Object.hasOwn(data,"editorCancel"))state.editPlan=false;
  else if(Object.hasOwn(data,"editorAdvanced")) {
    edit.advanced=!edit.advanced;
    if(edit.advanced){delete edit.jsonHard;delete edit.jsonSoft;}
  } else if(data.editorMove) {
    const [source,index]=editorNode(data.editorMove);
    const [item]=source.splice(index,1);
    const target=data.editorMove.startsWith("hard.")?"soft":"hard";
    if(["job_related_requirement","job_related_preference"].includes(item.field))item.field=target==="hard"?"job_related_requirement":"job_related_preference";
    edit[target].push(item);
  } else if(data.editorGroup) {
    edit[data.editorGroup]=[{operator:"AND",conditions:edit[data.editorGroup]}];
  } else if(data.editorAdd)edit.addTo=data.editorAdd;
  else if(Object.hasOwn(data,"editorInsert")) {
    const field=$("#editor-add-field").value;
    const defaults={education_level:["gte","bachelor"],major_group:["equals",""],major:["equals",""],
      hr_experience_months:["between",[0,12]],experience_months:["between",[0,12]],job_related_requirement:["verify",""]};
    let [operator,value]=defaults[field];
    edit[edit.addTo].push({field:field==="job_related_requirement" && edit.addTo==="soft"?"job_related_preference":field,
      operator,value,source:"人工修订"});
    edit.addTo="";
  } else return;
  updatePlanResult();
}
function editedConditions(form) {
  const edit=state.editPlan;
  const data=new FormData(form);
  const hard=edit.advanced?JSON.parse(data.get("hard")):edit.hard;
  const soft=edit.advanced?JSON.parse(data.get("soft")):edit.soft;
  const reason=String(data.get("reason") || "").trim();
  if(!reason)throw new Error("请填写修改理由。");
  function validate(item) {
    if(item?.conditions){item.conditions.forEach(validate);return;}
    if(!item || item.value==="" || item.value==null)throw new Error("请填写每条条件的取值。");
    if(item.operator==="between" && (!Array.isArray(item.value) || item.value.length!==2 ||
        item.value.some(value=>!Number.isInteger(value) || value<0) || item.value[0]>item.value[1]))
      throw new Error("最少月数不能大于最多月数，且必须是非负整数。");
  }
  if(!Array.isArray(hard) || !Array.isArray(soft))throw new Error("硬性要求和优先条件必须为列表。");
  [...hard,...soft].forEach(validate);
  return {hard_filters:hard,soft_preferences:soft,reason};
}
