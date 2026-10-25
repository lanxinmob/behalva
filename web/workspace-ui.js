'use strict';
function parseConversationPayload(text){
 const raw=String(text||'').trim(),prefix='Conversation:';
 if(!raw.startsWith(prefix))return null;
 try{const data=JSON.parse(raw.slice(prefix.length).trim());return Array.isArray(data)?data:null;}catch{return null;}
}
function summarizeConversationHistory(history){
 if(!history?.length)return '助手已根据对话记录回复';
 const lastUser=history.filter(m=>m.role==='user').at(-1);
 const content=lastUser?.content??lastUser?.text;
 if(typeof content==='string'&&content.trim()){
  const line=content.trim().replace(/\s+/g,' ');
  return line.length>160?`${line.slice(0,157)}…`:line;
 }
 return `已结合 ${history.length} 条对话记录生成回复`;
}
function formatDisplayText(text){
 if(text==null)return '';
 if(typeof text!=='string')return String(text);
 const trimmed=text.trim();if(!trimmed)return '';
 const history=parseConversationPayload(trimmed);
 if(history)return summarizeConversationHistory(history);
 if(trimmed.startsWith('{')||trimmed.startsWith('[')){
  try{
   const parsed=JSON.parse(trimmed);
   if(Array.isArray(parsed))return summarizeConversationHistory(parsed);
   if(parsed&&typeof parsed==='object'){
    for(const key of ['body','message','summary','answer','content','text']){
     if(typeof parsed[key]==='string'&&parsed[key].trim())return formatDisplayText(parsed[key]);
    }
   }
  }catch{/* keep */}
 }
 return text;
}
function taskDisplayTitle(task){
 const title=formatDisplayText(task?.title||'');
 if(task?.kind==='chat'){const short=title.slice(0,80);return short?`对话 · ${short}`:'对话任务';}
 const kind=taskKindNames[task?.kind];
 if(kind&&title)return `${kind} · ${title}`;
 return title||kind||'任务';
}
function greetingForNow(){
 const hour=new Date().getHours();
 if(hour<5)return '夜深了';if(hour<11)return '早上好';if(hour<14)return '中午好';if(hour<18)return '下午好';
 return '晚上好';
}
const pageEyebrows={home:'今日概览',chat:'连续对话',tasks:'执行与跟进',operations:'需要你确认',sources:'资料库',drafts:'写作草稿',goals:'方向与目标',schedules:'节奏与回访',rules:'经验与规则',tables:'结构化结果',settings:'连接与模型',guide:'上手引导'};
function statTile(label,value,nav,iconName){
 const tile=el('div',undefined,'stat-tile');
 const top=el('div',undefined,'stat-tile-top');
 top.append(icon(iconName),el('span',label,'stat-label'));
 tile.append(top,el('div',String(value),'stat-value'));
 if(nav&&value>0){
  tile.dataset.action=nav;tile.tabIndex=0;tile.setAttribute('role','button');tile.setAttribute('aria-label',`查看${label}`);
  const open=()=>go(nav);
  tile.onclick=open;tile.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();open();}};
 }
 return tile;
}
function renderHomeDashboard(box){
 const shell=el('div',undefined,'dashboard');
 const hero=el('section',undefined,'dashboard-hero');
 const pending=(state.operations||[]).filter(o=>o.state==='awaiting_approval');
 const activeTasks=(state.tasks||[]).filter(t=>t.state!=='completed');
 const needsYou=pending.length+activeTasks.filter(t=>['waiting_input','waiting_authorization','awaiting_approval','unknown','blocked'].includes(t.state)).length;
 hero.append(el('p',greetingForNow(),'dashboard-greeting'),el('h2',needsYou?'有事项等你拍板':'一切就绪，可以专注推进','dashboard-title'),el('p',state.model_configured?'下面是工作台状态与最近动态。':'模型尚未接入；你仍可整理资料、管理任务与草稿。','dashboard-lead muted'));
 const quick=el('div',undefined,'dashboard-quick');
 quick.append(action('开始聊天',()=>go('chat')),action('新任务',newTask,true),action('使用引导',()=>go('guide'),true));
 hero.append(quick);shell.append(hero);
 if(!state.model_configured){const strip=el('div',undefined,'setup-strip');strip.append(el('p','完成模型配置后即可收到助手回复。'),action('接入模型 API',()=>go('settings'),true));shell.append(strip);}
 const stats=el('div',undefined,'stat-grid summary');
 stats.append(statTile('进行中',activeTasks.length,'tasks','list-checks'),statTile('待确认',pending.length,'operations','circle-check'),statTile('资料',(state.sources||[]).length,'sources','files'));
 shell.append(stats);
 const budget=el('div',undefined,'budget-pill');
 budget.append(icon('wallet'),el('span',`本周预算预留 $${Number(state.budget?.committed_usd||0).toFixed(3)} / $${state.budget?.limit_usd??50}`),el('span','估算 · 非账单','muted'));
 shell.append(budget);
 if(pending.length||activeTasks.some(t=>!['running','queued','completed'].includes(t.state))){
  const urgent=el('section',undefined,'dashboard-panel');
  urgent.append(el('h3','需要我处理','panel-title'),el('p','优先处理确认与待补充事项。','muted'));
  pendingApprovalsSection(urgent,2);
  for(const t of activeTasks.filter(t=>['waiting_input','waiting_authorization','unknown','blocked'].includes(t.state)).slice(0,3))urgent.append(showTask(t,{list:true}));
  shell.append(urgent);
 }
 const recent=(state.tasks||[]).slice(0,6);
 if(recent.length){
  const activity=el('section',undefined,'dashboard-panel');
  const head=el('div',undefined,'panel-head row');
  head.append(el('h3','最近动态','panel-title'),action('全部任务',()=>go('tasks'),true));
  activity.append(head);
  const list=el('div',undefined,'task-list');
  for(const t of recent)list.append(showTask(t,{list:true}));
  activity.append(list);shell.append(activity);
 }else shell.append(el('div','还没有任务记录。从聊天或新任务开始。','empty-state'));
 box.append(shell);
}
function updatePageChrome(){
 $('#heading').textContent=names[page];
 $('#eyebrow').textContent=pageEyebrows[page]||'个人工作区';
 document.title=`${names[page]} · 个人助手`;
 const actions=$('#page-actions');if(!actions)return;
 actions.replaceChildren();
 if(page==='home')actions.append(action('＋ 新任务',newTask));
 if(page==='tasks')actions.append(action('＋ 新任务',newTask));
 if(page==='chat'){const fresh=action('＋ 新对话',freshConversation,true);fresh.classList.add('header-chip');actions.append(fresh);}
 if(page==='operations'&&(state.operations||[]).some(o=>o.state==='awaiting_approval'))actions.append(action('稍后处理',()=>{$('#notice').textContent='待确认项仍保留在列表中，你可以随时回来核对。';},true));
 if(page==='settings'){const pair=action('配对新手机',pairPhone,true);pair.classList.add('header-chip');actions.append(pair);}
}
let setupInfo=null,selectedTaskId=null,selectedOperationId=null,taskSearchFilter='';
function macSearchField(placeholder,onInput){
 const wrap=el('div',undefined,'mac-search-field');
 const input=el('input');input.type='search';input.placeholder=placeholder;input.setAttribute('aria-label',placeholder);
 input.addEventListener('input',()=>onInput?.(input.value));wrap.append(icon('search'),input);return {wrap,input};
}
function macGroupedList(rows){const g=el('div',undefined,'mac-grouped-list');for(const r of rows)g.append(r);return g;}
function macListRow({title,subtitle,selected,onClick,badgeState}){
 const row=el('button',undefined,'mac-list-row'+(selected?' selected':''));
 row.type='button';
 const main=el('div',undefined,'mac-list-row-main');
 main.append(el('span',title,'mac-list-title'));
 if(subtitle)main.append(el('span',subtitle,'mac-list-subtitle muted'));
 row.append(main);
 if(badgeState){const b=el('span',statusNames[badgeState]||badgeState,'mac-list-badge badge');b.dataset.state=badgeState;row.append(b);}
 row.onclick=onClick;return row;
}
function macSplitView(left,right,className=''){
 const root=el('div',undefined,'mac-split '+className);
 const l=el('div',undefined,'mac-split-pane mac-split-list');l.append(left);
 const r=el('div',undefined,'mac-split-pane mac-split-detail');r.append(right);
 root.append(l,r);return root;
}
function mountToolbarSearch(node){
 const slot=$('#toolbar-search-slot');if(!slot)return;
 slot.replaceChildren();if(node)slot.append(node);
}
function renderTasksSplit(box){
 if(matchMedia('(max-width:740px)').matches){
  const list=el('div',undefined,'task-list');
  if(!(state.tasks||[]).length)list.append(el('p','这里还没有记录。','empty'));
  for(const t of state.tasks||[])list.append(showTask(t,{list:true}));
  box.append(list);mountToolbarSearch(null);return;
 }
 const tasks=(state.tasks||[]);
 if(!selectedTaskId&&tasks[0])selectedTaskId=tasks[0].id;
 const {wrap:searchWrap,input:search}=macSearchField('搜索任务');
 search.value=taskSearchFilter;search.oninput=()=>{taskSearchFilter=search.value;render();};
 mountToolbarSearch(searchWrap);
 const q=taskSearchFilter.trim().toLowerCase();
 let filtered=tasks;
 if(q)filtered=tasks.filter(t=>taskDisplayTitle(t).toLowerCase().includes(q));
 const rows=filtered.map(t=>macListRow({title:taskDisplayTitle(t),subtitle:statusNames[t.state]||t.state,selected:t.id===selectedTaskId,badgeState:t.state,onClick:()=>{selectedTaskId=t.id;render();}}));
 const list=macGroupedList(rows);
 if(!rows.length)list.append(el('p','没有匹配的任务。','empty'));
 const detail=el('div',undefined,'mac-detail-scroll');
 const current=tasks.find(t=>t.id===selectedTaskId);
 if(current)detail.append(showTask(current));
 else{const empty=el('div',undefined,'mac-empty-detail');empty.append(el('h3','选择任务'),el('p','从左侧列表选择一项查看详情与操作。','muted'));detail.append(empty);}
 box.append(macSplitView(list,detail,'tasks-split'));
}
function renderOperationsSplit(box){
 const ops=state.operations||[];
 if(matchMedia('(max-width:740px)').matches){
  mountToolbarSearch(null);
  if(!ops.length)box.append(el('p','当前没有待确认操作。','empty'));
  for(const o of ops)box.append(approvalCard(o));
  return;
 }
 if(!selectedOperationId&&ops.length){
  const preferred=ops.find(o=>o.payload?.subject==='Fixture review')||ops.find(o=>(o.payload?.attachments||[]).length)||ops[0];
  selectedOperationId=preferred.id;
 }
 const rows=ops.map(o=>{
  const p=o.payload||{};
  const title=p.subject||p.url||(o.kind==='gmail'?'邮件':'表单');
  const sub=statusNames[o.state]||o.state;
  return macListRow({title,subtitle:sub,selected:o.id===selectedOperationId,badgeState:o.state,onClick:()=>{selectedOperationId=o.id;render();}});
 });
 const list=macGroupedList(rows);
 if(!rows.length)list.append(el('p','当前没有待确认操作。','empty'));
 const detail=el('div',undefined,'mac-detail-scroll');
 if(!ops.length)detail.append(el('p','当前没有待确认操作。','empty'));
 for(const o of ops){
  const sheet=approvalCard(o);
  if(o.id===selectedOperationId)sheet.classList.add('approval-sheet-selected');
  detail.append(sheet);
 }
 mountToolbarSearch(null);
 box.append(macSplitView(list,detail,'operations-split'));
}
const navIcons={home:'layout-dashboard',chat:'message-circle',tasks:'list-checks',operations:'circle-check',sources:'files',drafts:'file-pen-line',tables:'table-2',goals:'flag',schedules:'calendar-clock',rules:'book-open',settings:'settings-2',guide:'circle-help'};
function icon(name){const i=el('i');i.dataset.lucide=name;return i;}
function paintIcons(){if(window.lucide)lucide.createIcons();}
function closeNavigation(){document.body.classList.remove('nav-open');$('#menu-toggle').setAttribute('aria-expanded','false');$('#sidebar').inert=matchMedia('(max-width:740px)').matches;$('main').inert=false;}
function renderNavigation(){
 $('#sidebar').inert=matchMedia('(max-width:740px)').matches&&!document.body.classList.contains('nav-open');
 const nav=$('#nav');nav.replaceChildren();
 for(const [label,keys] of [['日常',['home','chat','tasks','operations']],['工作资料',['sources','drafts','tables']],['长期跟进',['goals','schedules','rules']],['工作区',['settings','guide']]]){
  nav.append(el('div',label,'nav-group'));
  for(const k of keys){const b=action(names[k],()=>{const mobile=matchMedia('(max-width:740px)').matches;go(k);closeNavigation();if(mobile)$('#menu-toggle').focus();},true);b.prepend(icon(navIcons[k]));b.className='nav-item'+(page===k?' selected':'');if(page===k)b.setAttribute('aria-current','page');nav.append(b);}
 }
 document.body.classList.toggle('chat-page',page==='chat'&&!!token);
 $('#workspace-status').classList.toggle('connected',!!token&&state.cursor!==undefined);
 $('#workspace-status').lastElementChild.textContent=token&&state.cursor!==undefined?'已连接工作区':'尚未连接工作区';
 renderMobileTabs();
 if(typeof updatePageChrome==='function')updatePageChrome();
}
function initShell(){
 $('#menu-toggle').onclick=()=>{const open=document.body.classList.toggle('nav-open');$('#menu-toggle').setAttribute('aria-expanded',String(open));$('#sidebar').inert=!open;$('main').inert=open;if(open)$('#nav button').focus();};
 $('#nav-backdrop').onclick=()=>{closeNavigation();$('#menu-toggle').focus();};
 $('#help').onclick=()=>go('guide');
 document.addEventListener('keydown',e=>{
  if(!document.body.classList.contains('nav-open'))return;
  if(e.key==='Escape'){closeNavigation();$('#menu-toggle').focus();}
  if(e.key==='Tab'){const buttons=Array.from($('#nav').querySelectorAll('button'));const first=buttons[0],last=buttons.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}
 });
 matchMedia('(max-width:740px)').addEventListener('change',()=>{closeNavigation();renderMobileTabs();});
}
function field(label,value){const row=el('div',undefined,'field');row.append(el('dt',label),el('dd',String(value??'未提供')));return row;}
const taskKindNames={research:'调查任务',feature:'功能需求',chat:'对话'};
const citationStatusNames={references_validated:'引用已核对',missing:'缺少来源标注',invalid:'存在无效引用'};
function readableResultText(result,notice){
 const fromResult=result&&['body','message','summary','answer'].map(k=>result[k]).find(v=>typeof v==='string'&&v.trim());
 if(fromResult)return formatDisplayText(fromResult.trim());
 if(typeof notice==='string'&&notice.trim())return formatDisplayText(notice.trim());
 return result?'处理记录已保存，可展开查看详情。':'';
}
function renderMobileTabs(){
 const bar=$('#mobile-tabs');if(!bar)return;
 bar.replaceChildren();
 if(!token||matchMedia('(min-width:741px)').matches)return;
 const tabs=[['home','工作台','layout-dashboard'],['chat','聊天','message-circle'],['tasks','任务','list-checks'],['operations','待确认','circle-check']];
 for(const [key,label,ico] of tabs){
  const b=action(label,()=>{go(key);closeNavigation();},true);
  b.className='mobile-tab'+(page===key?' selected':'');
  b.setAttribute('aria-label',`底部导航：${label}`);
  b.prepend(icon(ico));
  if(page===key)b.setAttribute('aria-current','page');
  bar.append(b);
 }
}
function taskModelRuns(task){return (state.model_runs||[]).filter(r=>r.task_id===task.id);}
function taskCostUsd(task){
 if(task.result&&typeof task.result.cost_usd==='number')return task.result.cost_usd;
 let sum=0,found=false;
 for(const run of taskModelRuns(task)){const c=run.result?.cost_usd;if(typeof c==='number'){sum+=c;found=true;}}
 return found?sum:null;
}
function formatTaskUsd(value){return value==null?'—':`$${Number(value).toFixed(4)}`;}
function taskCitationSummary(task){
 const r=task.result;if(!r)return null;
 const status=r.citation_status,label=citationStatusNames[status]||status;
 const cites=(r.citations||[]).length,invalid=(r.invalid_citations||[]).length;
 if(!status&&!cites&&!invalid)return null;
 let text=label||(cites?`${cites} 条来源引用`:'无引用');
 if(invalid)text+=` · ${invalid} 条无法核对`;
 return {text,cites:r.citations||[],invalid:r.invalid_citations||[]};
}
function appendTaskMeta(card,task){
 const meta=el('dl',undefined,'task-meta');
 meta.append(field('状态',statusNames[task.state]||task.state));
 const cost=taskCostUsd(task);
 if(cost!=null||taskModelRuns(task).length)meta.append(field('模型费用（估算）',formatTaskUsd(cost)));
 const cite=taskCitationSummary(task);
 if(cite){
  meta.append(field('来源与引用',cite.text));
  if(cite.cites.length){
   const list=el('ul',undefined,'citation-list');
   for(const c of cite.cites.slice(0,6)){const li=el('li',c.uri||c.source_id||'来源');if(c.locator)li.title=c.locator;list.append(li);}
   if(cite.cites.length>6)list.append(el('li',`另有 ${cite.cites.length-6} 条…`,'muted'));
   const row=el('div',undefined,'field citation-list-wrap');row.append(el('dt','引用明细'),el('dd'));row.querySelector('dd').append(list);meta.append(row);
  }
 }
 if(task.notice&&task.notice!==task.result?.message)meta.append(field('说明',task.notice));
 card.append(meta);
}
function rawJsonExpander(parent,label,obj){
 const d=el('details',undefined,'raw-json');d.append(el('summary',label));
 const pre=el('pre',JSON.stringify(obj,null,2));pre.tabIndex=0;pre.setAttribute('role','region');pre.setAttribute('aria-label',label);
 d.append(pre);parent.append(d);return d;
}
function pendingApprovalsSection(box,limit){
 const pending=(state.operations||[]).filter(o=>o.state==='awaiting_approval');
 if(!pending.length)return;
 const section=el('section',undefined,'pending-approvals');
 section.append(el('h2','待你确认','section-title'),el('p',`${pending.length} 项发送或提交需要你核对正文、附件与目标后授权。`,'muted'));
 for(const o of pending.slice(0,limit??pending.length))section.append(approvalCard(o));
 if(limit&&pending.length>limit)section.append(el('p',`另有 ${pending.length-limit} 项待确认。`,'muted'));
 if(limit&&pending.length>limit)section.append(action('查看全部待确认',()=>go('operations'),true));
 box.append(section);
}
function approvalCard(o){
 const p=o.payload,missingSnapshot=!(p.attachments||[]).length&&(p.attachment_ids||[]).length>0;
 const c=el('article',undefined,'card approval-sheet');
 const head=el('header',undefined,'approval-head');
 const type=el('div',undefined,'approval-type');
 type.append(icon(o.kind==='gmail'?'mail':'file-input'),el('span',o.kind==='gmail'?'发送邮件':'提交表单'));
 const badge=el('span',statusNames[o.state]||o.state,'badge');badge.dataset.state=o.state;
 head.append(type,badge);c.append(head);
 const hero=el('div',undefined,'approval-hero');
 if(p.subject)hero.append(el('h3',p.subject));
 const meta=el('div',undefined,'approval-meta');
 if(p.to){
  const row=el('div',undefined,'approval-meta-row');
  row.append(el('span','收件人','approval-meta-key muted'),el('span',p.to,'approval-recipient'));
  meta.append(row);
 }
 if(p.from){
  const row=el('div',undefined,'approval-meta-row');
  row.append(el('span','发件人','approval-meta-key muted'),el('span',p.from));
  meta.append(row);
 }
 if(p.url){
  const row=el('div',undefined,'approval-meta-row');
  row.append(el('span','链接','approval-meta-key muted'),el('span',p.url,'uri'));
  meta.append(row);
 }
 hero.append(meta);c.append(hero);
 const bodyText=typeof p.body==='string'?p.body:(p.fields?JSON.stringify(p.fields,null,2):'');
 if(bodyText){
  const preview=el('div',undefined,'approval-body-preview');
  const lines=bodyText.split('\n');const short=lines.slice(0,4).join('\n')+(lines.length>4?'\n…':'');
  preview.append(el('p',short,'body'));
  const fullWrap=el('details',undefined,'approval-body-expand');
  fullWrap.append(el('summary','查看完整正文'));
  const bodyField=el('div',undefined,'field');
  bodyField.append(el('dt','完整正文'),el('dd',bodyText));
  fullWrap.append(bodyField);c.append(preview,fullWrap);
 }else{
  const bodyField=el('div',undefined,'field');
  bodyField.append(el('dt','完整正文'),el('dd','—'));c.append(bodyField);
 }
 const chips=el('div',undefined,'file-chips');
 for(const a of p.attachments||[]){
  const chip=el('div',undefined,'file-chip');
  chip.setAttribute('aria-label',a.name);
  const sizeKb=Math.max(1,Math.round(a.size/1024));
  chip.append(icon('paperclip'),el('span',a.name,'file-chip-name'),el('span',`${sizeKb} KB`,'muted file-chip-size'));
  const hash=el('details',undefined,'file-chip-hash');
  hash.append(el('summary','校验信息'),el('code',a.sha256));
  chip.append(hash);chips.append(chip);
 }
 if(chips.childElementCount)c.append(chips);
 const attachField=el('div',undefined,'field approval-attach-field');
 if((p.attachments||[]).length){
  const lines=(p.attachments||[]).map(a=>`SHA-256：${a.sha256}`).join('\n');
  attachField.append(el('dt','附件'),el('dd',lines));
 }else attachField.append(el('dt','附件'),el('dd',missingSnapshot?'附件快照缺失，无法核对，请重新准备此操作。':'无'));
 c.append(attachField);
 if(p.cc||p.bcc||p.account){
  const extra=el('details',undefined,'approval-extra');
  extra.append(el('summary','更多字段'));
  const dl=el('dl');
  if(p.cc)dl.append(field('抄送',p.cc));
  if(p.bcc)dl.append(field('密送',p.bcc));
  if(p.account)dl.append(field('使用账号',p.account));
  extra.append(dl);c.append(extra);
 }
 c.append(el('p',o.evidence||'尚无外部完成证据','muted approval-evidence'));
 const actions=el('div',undefined,'actions approval-actions');
 if(o.state==='awaiting_approval'&&!missingSnapshot){
  actions.append(action('确认以上内容并授权执行',async()=>{if(confirm('确认目标、全部正文、附件和字段都正确？这将授权实际发送或提交。')){await api(`operations/${o.id}/approve`,{revision:o.revision,payload_hash:o.payload_hash});await sync();}}));
  actions.append(action('稍后处理',()=>{$('#notice').textContent='已保留在待确认列表，稍后可继续核对。';},true));
 }
 c.append(actions);
 return c;
}
function setupPanel(box){
 const section=el('section',undefined,'settings-section');section.id='phone-setup';section.append(el('h2','手机连接步骤'));
 section.append(el('p','手机使用设备令牌；模型 API Key 只配置在电脑。','muted'));
 const info=el('div');section.append(info);box.append(section);
 const draw=()=>{
  info.replaceChildren();
  if(!setupInfo){info.append(el('p','连接后可查看当前工作区的配置位置。','muted'));return;}
  const dl=el('dl');dl.append(field('当前网页地址',location.origin),field('监听范围',setupInfo.listen_host==='127.0.0.1'?'仅电脑本机。手机需先建立局域网或私人远程连接。':setupInfo.listen_host));
  if(setupInfo.fingerprint.trim())dl.append(field('自签名证书 SHA-256 指纹',setupInfo.fingerprint.trim()));
  dl.append(field('指纹文件位置',setupInfo.fingerprint_path));
  const steps=el('ol'),first=el('li','安装并登录：电脑安装 Windows 版 Tailscale，手机安装 Android 版，两端登录同一个账号。电脑右下角托盘打开 Tailscale 登录；手机打开 Tailscale，登录后打开连接开关并允许 VPN。');
  for(const [title,url] of [['下载 Windows 版','https://tailscale.com/download/windows'],['下载 Android 版','https://tailscale.com/download/android']]){const a=el('a',title);a.href=url;a.target='_blank';a.rel='noopener noreferrer';const line=el('p');line.append(a);first.append(line);}
  steps.append(first);
  const network=el('li','取得 HTTPS 地址：电脑开始菜单搜索 PowerShell 并打开，复制执行下面两行。无需进入项目目录。');
  const d=el('details');d.open=true;d.append(el('summary','推荐：取得 Tailscale HTTPS 地址'));
  const command=`& "$env:ProgramFiles\\Tailscale\\tailscale.exe" serve --bg ${setupInfo.tls?'https+insecure://localhost:':'http://127.0.0.1:'}${setupInfo.port}\n& "$env:ProgramFiles\\Tailscale\\tailscale.exe" serve status`;
  d.append(el('pre',command));d.append(action('复制电脑连接命令',async()=>{await navigator.clipboard.writeText(command);$('#notice').textContent='已复制，打开电脑 PowerShell 粘贴执行。';},true));
  d.append(el('p','首次运行若提示开启 HTTPS，打开命令输出的管理链接，在 Tailscale 控制台开启 HTTPS（需要时开启 MagicDNS），然后重新执行命令。找到 “Available within your tailnet” 下的 https://电脑名.…ts.net 地址，只复制地址本身，不要复制下面的本机转发地址。以后用第二行命令可再次查看。'));
  d.append(el('p','Serve 只允许私人 Tailscale 网络访问。手机连接时，电脑助手服务和两端 Tailscale 都需运行。Android 同时只能运行一个 VPN；使用其他 VPN 时需切换到 Tailscale。'));
  const link=el('a','Serve 官方步骤');link.href='https://tailscale.com/docs/reference/tailscale-cli/serve';link.target='_blank';link.rel='noopener noreferrer';d.append(link);
  network.append(d);steps.append(network);
  const second=el('li','配对手机：点击下面按钮，设备名称填“我的手机”，HTTPS 地址填刚复制的 https://…ts.net，证书方式保持“可信证书”，点击“生成连接配置”后“复制连接配置”。令牌由助手自动生成，不需要自己找或填写。');second.append(action('配对新手机',pairPhone));steps.append(second);
  steps.append(el('li','手机导入：通过仅自己可见的方式把配置传到手机并复制完整内容；打开助手 App，进入“更多 → 设置 → 粘贴连接配置”，核对地址后保存，再点击同步。成功后顶部显示已同步。Tailscale 证书由它自动取得，指纹留空。'));info.append(steps);
  const lan=el('details');lan.append(el('summary','同一 Wi-Fi 连接'));
  lan.append(el('p','电脑和手机在同一 Wi-Fi 时可填 https://电脑局域网IP:'+setupInfo.port+'。服务必须监听局域网地址，且防火墙允许该端口；当前仅监听本机时不能直连。证书由工作区启动程序自动生成，无需自行购买或制作；配对时选“工作区自签名证书”，使用下面的 SHA-256 指纹。切勿将私钥 server.key 传到手机。校园网可能隔离设备。'));lan.append(dl);info.append(lan);
 };
 draw();if(token&&!setupInfo)api('setup').then(x=>{setupInfo=x;if(section.isConnected)draw();}).catch(e=>{if(section.isConnected)info.append(el('p','未取得配置位置：'+e.message,'form-error'));});
}

function phoneConnection(url,deviceToken,fingerprint){
 const u=new URL(url.trim());
 if(u.protocol!=='https:'||!u.hostname||u.username||u.password||u.search||u.hash||u.pathname!=='/'||['localhost','localhost.','[::1]'].includes(u.hostname.toLowerCase())||u.hostname.startsWith('127.'))throw new Error('请填手机可达的 HTTPS 地址，不能使用 127.0.0.1 / localhost，也不能包含路径或登录信息。');
 const pin=fingerprint.trim().replaceAll(':','').toLowerCase();
 if(pin&&!/^[a-f0-9]{64}$/.test(pin))throw new Error('证书指纹需要完整的 SHA-256（64 位十六进制）。');
 return {version:1,url:u.origin,token:deviceToken,fingerprint:pin};
}
function pairPhone(){
 const dialog=el('dialog');dialog.id='phone-pairing';dialog.append(el('h2','配对新手机'));
 dialog.append(el('p','先取得手机可达的地址，再签发凭据。连接配置含设备令牌；仅交给你自己的手机，勿放入聊天、公开仓库或公开链接。','muted'));
 const inputs={};
 for(const [key,label,value] of [['name','设备名称','我的手机'],['url','手机可达的 HTTPS 地址',''],['fingerprint','证书 SHA-256 指纹（自签名时必填）',setupInfo?.fingerprint?.trim()||'']]){
  const l=el('label',label),input=el('input');input.value=value;input.setAttribute('aria-label',label);l.append(input);inputs[key]=input;dialog.append(l);
 }
 const label=el('label','证书方式'),mode=el('select');mode.setAttribute('aria-label','证书方式');
 for(const [value,text] of [['trusted','可信证书（推荐 Tailscale）'],['pinned','工作区自签名证书']]){const o=el('option',text);o.value=value;mode.append(o);}label.append(mode);dialog.insertBefore(label,inputs.fingerprint.parentElement);
 const update=()=>{inputs.fingerprint.parentElement.hidden=mode.value==='trusted';};mode.onchange=update;update();
 const error=el('p',undefined,'form-error');error.setAttribute('role','alert');dialog.append(error);
 const result=el('section');dialog.append(result);const actions=el('div',undefined,'actions');
 const generate=action('生成连接配置',async()=>{
  try{
   const name=inputs.name.value.trim();if(!name)throw new Error('请填写设备名称。');
   const pin=mode.value==='pinned'?inputs.fingerprint.value:'';
   if(mode.value==='pinned'&&!pin.trim())throw new Error('自签名证书必须填写指纹。');
   const connection=phoneConnection(inputs.url.value,'',pin);
   const device=await api('devices',{name});connection.token=device.token;
   generate.hidden=true;for(const input of [...Object.values(inputs),mode])input.disabled=true;
   result.append(el('h3','连接配置已生成'),el('p','只在此处提供这一次。手机粘贴导入后核对地址；若丢失配置，请撤销这台设备再重新配对。','muted'));
   const data=JSON.stringify(connection,null,2),textarea=el('textarea');textarea.readOnly=true;textarea.setAttribute('aria-label','手机连接配置');textarea.value=data;result.append(textarea);
   const row=el('div',undefined,'actions');row.append(action('复制连接配置',async()=>{await navigator.clipboard.writeText(data);error.textContent='已复制。配置包含设备令牌，手机导入后请清理中转副本。';},true),action('下载连接配置',()=>download('phone-connection.json',data),true));result.append(row);
   state=await api('state');render();error.textContent='';
  }catch(e){error.textContent=e.message;}
 });
 actions.append(generate,action('关闭',()=>dialog.close(),true));dialog.append(actions);dialog.onclose=()=>dialog.remove();document.body.append(dialog);dialog.showModal();inputs.url.focus();
}
