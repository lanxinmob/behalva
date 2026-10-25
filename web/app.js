'use strict';
let token=sessionStorage.getItem('pa.token')||'', state={}, page='home';
const names={home:'工作台',chat:'聊天',guide:'使用引导',tasks:'任务',sources:'资料',drafts:'草稿',operations:'待确认',goals:'长期目标',schedules:'定期跟进',rules:'记忆与规则',tables:'表格',settings:'连接与授权'};
const statusNames={queued:'等待执行',running:'进行中',completed:'已完成',waiting_input:'需要补充',waiting_authorization:'需要资料授权',blocked:'等待配置或接入',failed:'未完成',awaiting_approval:'等待确认',approved:'已批准',executing:'执行中',unknown:'结果待核实',succeeded:'已核实成功',proposed:'规则候选',active:'进行中'};
const $=s=>document.querySelector(s);
function el(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;}
function action(label,fn,secondary=false){const b=el('button',label,secondary?'secondary':'primary');b.onclick=async()=>{b.disabled=true;try{await fn();}catch(e){$('#notice').textContent=e.message;}finally{b.disabled=false;}};return b;}
async function api(path,body,method){const r=await fetch('/api/'+path,{method:method||(body?'POST':'GET'),headers:{'Authorization':'Bearer '+token,'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});const j=await r.json();if(!r.ok){const e=new Error(j.error+(j.details&&Object.keys(j.details).length?'\n'+JSON.stringify(j.details,null,2):''));e.status=r.status;e.data=j;throw e;}return j;}
async function sync(){
 const next=await api('state'),input=$('#chat-input');
 if(input?.dataset.composing==='true'){
  // Keep the native IME session intact, then fetch a fresh snapshot before rendering.
  await new Promise(resolve=>{
   const observer=new MutationObserver(()=>{if(!input.isConnected)done();});
   function done(){observer.disconnect();input.removeEventListener('compositionend',done);input.removeEventListener('blur',done);delete input.dataset.composing;resolve();}
   input.addEventListener('compositionend',done,{once:true});
   input.addEventListener('blur',done,{once:true});
   observer.observe(document.body,{childList:true,subtree:true});
  });
  return sync();
 }
 const focused=document.activeElement===input,start=input?.selectionStart,end=input?.selectionEnd,scroller=$('.chat-scroll'),scroll=scroller?.scrollTop||0;
 const maxScroll=scroller?Math.max(0,scroller.scrollHeight-scroller.clientHeight):0;
 const atEnd=scroller&&maxScroll>0&&maxScroll-scroll<50;
 if(typeof preserveChatScrollTop!=='undefined')preserveChatScrollTop=page==='chat'&&!atEnd?scroll:null;
 state=next;$('#connection').hidden=true;$('#notice').textContent='';render();
 if(focused){const replacement=$('#chat-input');replacement?.focus({preventScroll:true});replacement?.setSelectionRange(Math.min(start,replacement.value.length),Math.min(end,replacement.value.length));}
 const restore=()=>{const replacement=$('.chat-scroll');if(replacement&&!atEnd)replacement.scrollTop=scroll;};
 requestAnimationFrame(()=>requestAnimationFrame(restore));
}
function form(title,fields,save){
 $('#editor-title').textContent=title;
 const box=$('#editor-fields');box.replaceChildren();const controls={};
 for(const f of fields){
  const label=el('label',f.label);
  const input=el(f.options?'select':(f.multiline?'textarea':'input'));
  input.setAttribute('aria-label',f.label);
  if(f.options){
   input.multiple=!f.single;
   for(const o of f.options){
    const option=el('option',o.title);option.value=o.id;
    option.selected=Array.isArray(f.value)?f.value.includes(o.id):f.value===o.id;
    input.append(option);
   }
  }else {if(f.type)input.type=f.type;input.value=f.value??'';}
  label.append(input);box.append(label);controls[f.name]=input;
 }
 $('#editor-save').onclick=async()=>{
  const b=$('#editor-save');b.disabled=true;
  try{
   await save(Object.fromEntries(Object.entries(controls).map(([k,v])=>[k,v.multiple?Array.from(v.selectedOptions,x=>x.value):v.value])));
   $('#editor').close();await sync();
  }catch(e){
   if(e.data?.details?.conflict){$('#editor').close();await sync().catch(()=>{});$('#notice').textContent=e.message;}
   else {let error=$('#editor-error');if(!error){error=el('p',undefined,'form-error');error.id='editor-error';error.setAttribute('role','alert');box.append(error);}error.textContent=e.message;}
  }
  finally{b.disabled=false;}
 };
 const dlg=$('#editor');dlg.classList.add('mac-sheet');dlg.showModal();
}
function card(title,body,badge){const c=el('article',undefined,'card');if(badge){const b=el('span',statusNames[badge]||badge,'badge');b.dataset.state=badge;c.append(b);}c.append(el('h3',title));if(body)c.append(el('div',body,'body'));return c;}
function list(items,draw){const box=$('#content');if(!items.length)box.append(el('p','这里还没有记录。','empty'));items.forEach(x=>box.append(draw(x)));}
function newTask(){form('交给助手的新任务',[{name:'title',label:'你希望得到什么结果？',multiline:true},{name:'urls',label:'相关链接（每行一个，可选）'},{name:'sources',label:'关联资料（可多选，云端使用另行授权）',options:state.sources||[]}],v=>api('tasks',{source_ids:v.sources,title:v.title,urls:v.urls.split('\n').map(x=>x.trim()).filter(Boolean),request_id:crypto.randomUUID()}));}
function taskMetaChips(task){
 const chips=el('div',undefined,'task-chips');
 const st=el('span',statusNames[task.state]||task.state,'chip');st.dataset.state=task.state;chips.append(st);
 const cost=taskCostUsd(task);
 if(cost!=null)chips.append(el('span',formatTaskUsd(cost),'chip chip-muted'));
 const cite=taskCitationSummary(task);
 if(cite)chips.append(el('span',cite.text,'chip chip-muted'));
 return chips;
}
function buildTaskDetail(t){
 const panel=el('div',undefined,'task-detail');
 const summary=readableResultText(t.result,t.notice);
 if(summary)panel.append(el('div',summary,'body task-summary'));
 else if(t.notice)panel.append(el('p',formatDisplayText(t.notice),'muted'));
 appendTaskMeta(panel,t);
 if(t.result){
  const full=readableResultText(t.result);
  if(full&&full!==summary){
   const more=el('details');more.append(el('summary','查看完整结果'),el('div',full,'body'));panel.append(more);
  }
  if(t.result.evidence)panel.append(field('完成证据',t.result.evidence));
 }
 if(t.kind==='feature')panel.append(el('p','待开发需求 · 不会自动修改程序','muted'));
 const actions=el('div',undefined,'actions task-actions');
 if(t.kind==='chat')actions.append(action('打开对话',()=>{conversationId=t.conversation_id;go('chat');},true));
 if(t.kind!=='feature'&&!['running','completed','queued'].includes(t.state)){
  if(t.kind!=='chat')actions.append(action('补充并继续',()=>form('补充信息',[{name:'reply',label:'回复',multiline:true}],v=>api(`tasks/${t.id}/resume`,{...v,request_id:crypto.randomUUID()})),true));
  const unknownRuns=taskModelRuns(t).filter(r=>r.state==='unknown'&&!r.retry_authorized);
  if(unknownRuns.length){
   const advanced=el('details',undefined,'task-advanced');
   advanced.append(el('summary','模型调用待核实'));
   for(const run of unknownRuns){
    advanced.append(el('p',run.notice||'模型调用结果待核实','muted'));
    advanced.append(action('确认后重新调用模型',()=>form('模型调用恢复',[{name:'reason',label:'核查结果或重新调用的理由',multiline:true}],async v=>{
     if(!confirm('原调用可能已收费，原费用预留不会取消。确认重新调用并承担一次新调用费用？'))return;
     await api(`model-runs/${run.id}/retry`,{reason:v.reason,accept_unknown_cost:true,expected_revision:run.revision});
    }),true));
   }
   panel.append(advanced);
  }
 }
 if(actions.childElementCount)panel.append(actions);
 rawJsonExpander(panel,'原始详情',t);
 return panel;
}
function showTask(t,opts={}){
 const list=opts.list||opts.compact;
 if(list){
  const row=el('details',undefined,'task-row task-card');if(opts.compact)row.classList.add('task-card-compact');
  const summary=el('summary',undefined,'task-row-summary');
  const chevron=icon('chevron-right');chevron.classList.add('task-row-chevron');
  summary.append(el('div',taskDisplayTitle(t),'task-row-title'),taskMetaChips(t),chevron);
  row.append(summary,buildTaskDetail(t));
  return row;
 }
 const c=card(taskDisplayTitle(t),undefined,t.state);c.classList.add('task-card');
 c.append(buildTaskDetail(t));
 return c;
}
function editSchedule(s){
 const next=new Date((s?.next_at||Math.floor(Date.now()/1000)+3600)*1000);
 const local=new Date(next.getTime()-next.getTimezoneOffset()*60000).toISOString().slice(0,16);
 form(s?'编辑定期跟进':'新定期跟进',[
  {name:'title',label:'调查任务或追问内容',value:s?.title,multiline:true},
  {name:'kind',label:'类型',single:true,options:[{id:'research',title:'定期调查'},{id:'check_in',title:'询问进展'}],value:s?.kind||'research'},
  {name:'hours',label:'间隔（小时，最少 0.25）',type:'number',value:String((s?.interval_seconds||86400)/3600)},
  {name:'next',label:'首次／下次时间',type:'datetime-local',value:local},
  {name:'urls',label:'关联链接（每行一个）',multiline:true,value:(s?.urls||[]).join('\n')},
  {name:'sources',label:'关联资料',options:state.sources||[],value:s?.source_ids||[]}
 ],v=>{
  const hours=Number(v.hours),nextAt=Math.floor(new Date(v.next).getTime()/1000);
  if(!Number.isFinite(hours)||hours<0.25||hours>8784||!Number.isFinite(nextAt))throw new Error('请检查间隔和时间。');
  return api('schedules',{...(s?{id:s.id,expected_revision:s.revision}:{}),title:v.title,kind:v.kind,interval_seconds:Math.round(hours*3600),next_at:nextAt,active:s?.active??true,urls:v.urls.split('\n').map(x=>x.trim()).filter(Boolean),source_ids:v.sources,request_id:crypto.randomUUID()});
 });
}
function render(){const box=$('#content');box.replaceChildren();box.className='mac-content'+(page==='chat'?' mac-content-chat':'');renderNavigation();
 if(page!=='tasks'){taskSearchFilter='';mountToolbarSearch(null);}
 if(page==='guide')guide(box);
 if(page==='chat')renderChat(box);
 if(page==='home')renderHomeDashboard(box);
 if(page==='settings')renderSettings(box);
 if(page==='tasks')renderTasksSplit(box);
 if(page==='operations')renderOperationsSplit(box);
 if(page==='sources')for(const s of (state.sources||[]).filter(x=>x.available===false))box.append(card(s.title+' · 当前资料不可用',s.availability_reason+'\n保留的原文仅作为历史记录，已退出当前检索与模型上下文。','blocked'));
 if(page==='sources'){for(const a of state.attachments||[])box.append(card(a.name,`已保存附件 · ${a.size} 字节`));const search=el('input');search.placeholder='搜索原文，例如：报名截止';const results=el('div');box.append(search,action('查找原文',async()=>{results.replaceChildren();for(const hit of await api('search?q='+encodeURIComponent(search.value))){const c=card(hit.meta.title,hit.excerpt);c.append(el('p',`第 ${hit.line} 行 · 版本 ${hit.id}`,'muted'),el('p',hit.meta.uri,'uri'));results.append(c);}}),results,el('hr'));box.append(action('导入文字',()=>form('导入资料',[{name:'title',label:'标题'},{name:'body',label:'原文',multiline:true}],v=>api('documents',{...v,kind:'shared',uri:'shared:'+crypto.randomUUID()}))));list(state.sources||[],s=>{const c=card(s.title,s.preview,s.kind);c.append(el('p',s.uri,'uri'));c.append(action('查看当前原文',async()=>{const v=await api('versions/'+s.version_id);form('原文（保存将不修改资料）',[{name:'text',label:s.uri,value:v.body,multiline:true}],async()=>{});},true));if(!s.cloud_default)c.append(action('允许此资料用于云端模型',async()=>{if(confirm('允许这份资料按任务需要发送给配置的云端模型？')){await api('grants',{scope:'source',target:s.id,active:true});await sync();}},true));return c;});}
 if(page==='drafts'){box.append(action('＋ 新草稿',()=>form('新草稿',[{name:'title',label:'标题'},{name:'body',label:'正文',multiline:true}],v=>api('drafts',v))));list(state.drafts||[],d=>{const c=card(d.title,d.body);c.append(action('编辑',()=>form('编辑草稿',[{name:'title',label:'标题',value:d.title},{name:'body',label:'正文',value:d.body,multiline:true}],v=>api('drafts/'+d.id,{...v,expected_revision:d.revision},'PUT')),true));c.append(action('准备邮件',()=>form('准备邮件，随后仍需确认发送',[{name:'from',label:'发送账号（Gmail 地址）'},{name:'to',label:'收件人'},{name:'subject',label:'主题',value:d.title},{name:'body',label:'正文',value:d.body,multiline:true},{name:'attachment_ids',label:'选择邮件附件',options:(state.attachments||[]).map(a=>({...a,title:a.name}))}],v=>api('operations',{kind:'gmail',payload:v})),true));return c;});for(const f of (state.conflicts||[]).filter(x=>x.state==='unresolved')){const displayedRemote=state.drafts.find(d=>d.id===f.draft_id);const c=card('草稿冲突：选择需要保留的版本',undefined,'waiting_input');const versions=el('dl');versions.append(field('手机或本地版本',f.local?.body||f.local?.title||'—'),field('服务器当前版本',displayedRemote?.body||displayedRemote?.title||'—'));c.append(versions);for(const [choice,label] of [['local','选择本地版本'],['remote','保留服务器版本']])c.append(action(label,async()=>{const current=displayedRemote;await api(`conflicts/${f.id}/resolve`,{choice,expected_revision:current.revision});await sync();},true));box.append(c);}}
 if(page==='goals'){box.append(action('＋ 长期目标',()=>form('新目标',[{name:'title',label:'目标'},{name:'next_step',label:'下一步',multiline:true}],v=>api('goals',v))));list(state.goals||[],g=>card(g.title,g.next_step,g.state));}
 if(page==='schedules'){box.append(action('＋ 定期跟进',()=>editSchedule(null)));list(state.schedules||[],s=>{const c=card(s.title,`${s.kind==='research'?'定期调查':'询问进展'} · 每 ${s.interval_seconds/3600} 小时\n下次：${new Date(s.next_at*1000).toLocaleString()}\n${s.active?'已启用':'已暂停'}`);c.append(action('编辑',()=>editSchedule(s),true),action(s.active?'暂停':'启用',async()=>{await api('schedules',{...s,active:!s.active,expected_revision:s.revision,request_id:crypto.randomUUID()});await sync();},true));return c;});}
 if(page==='rules'){box.append(action('提出一条纠正规则',()=>form('候选规则',[{name:'title',label:'规则名称'},{name:'body',label:'适用范围、行为与例子',multiline:true}],v=>api('rules',v))));list(state.rules||[],r=>{const c=card(r.title,r.body,r.state);if(r.state==='proposed')c.append(action('批准并在后续任务生效',async()=>{await api(`rules/${r.id}/approve`,{revision:r.revision,hash:r.hash});await sync();}));return c;});}
 if(page==='tables')list(state.tables||[],t=>{const c=card(t.title);const table=el('table');table.setAttribute('aria-label',t.title);const head=el('tr');t.columns.forEach(x=>{const th=el('th',String(x));th.scope='col';head.append(th);});table.append(head);t.rows.forEach(row=>{const tr=el('tr');row.forEach(x=>tr.append(el('td',typeof x==='string'?x:JSON.stringify(x))));table.append(tr);});const wrap=el('div',undefined,'table-scroll');wrap.tabIndex=0;wrap.setAttribute('role','region');wrap.setAttribute('aria-label',t.title+'，可横向滚动');wrap.append(table);c.append(wrap);c.append(action('下载 Markdown',()=>download(t.title+'.md','| '+t.columns.join(' | ')+' |\n| '+t.columns.map(()=> '---').join(' | ')+' |\n'+t.rows.map(r=>'| '+r.map(x=>String(x).replaceAll('|','\\|').replaceAll('\n','<br>')).join(' | ')+' |').join('\n')),true));return c;});
 paintIcons();
}
function download(name,text){const a=el('a');a.href=URL.createObjectURL(new Blob([text],{type:'text/markdown;charset=utf-8'}));a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}
$('#connect').onclick=async()=>{token=$('#token').value;sessionStorage.setItem('pa.token',token);try{await sync();}catch(e){$('#notice').textContent=e.message;}};
$('#refresh').onclick=()=>sync().catch(e=>$('#notice').textContent=e.message);
initShell();
if(token)sync().catch(e=>$('#notice').textContent=e.message);
else {page='guide';render();}
