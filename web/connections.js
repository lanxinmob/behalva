'use strict';
let connectionsReport=null,connectionsError='',connectionsPoll=null;
const openConnections=new Set();
const connectionStateNames={not_configured:'未配置',unverified:'未验证',verified:'已验证',error:'异常'};
const connectionMeta={
 model:{title:'模型服务',icon:'cpu'},
 gmail:{title:'Gmail',icon:'mail'},
 qq:{title:'QQ 群 · OneBot',icon:'messages-square'},
 phone:{title:'手机 · Tailscale',icon:'smartphone'},
 worker:{title:'本机执行进程',icon:'activity'}
};
const gmailCheckNotes={
 requested:'验证请求已提交，等待本机执行进程领取。',
 checking:'正在读取账号资料…',
 expired:'执行进程未在 10 分钟内领取验证请求；不会自动重试，可再次验证。',
 unanswered:'验证没有返回结果；不会自动重试，可再次验证。'
};
function connectionTime(seconds){
 if(!seconds)return '—';
 const d=new Date(seconds*1000),diff=Date.now()/1000-seconds;
 if(diff<60)return '刚刚';
 if(diff<3600)return `${Math.floor(diff/60)} 分钟前`;
 const sameDay=d.toDateString()===new Date().toDateString();
 return sameDay?d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}):d.toLocaleString([], {month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'});
}
function connectionFacts(rows){
 const dl=el('dl',undefined,'conn-facts');
 for(const [label,value] of rows){if(value===undefined||value===null||value==='')continue;const row=el('div',undefined,'conn-fact');row.append(el('dt',label),el('dd',String(value)));dl.append(row);}
 return dl;
}
function connectionStatePill(state){
 const pill=el('span',undefined,'conn-state');pill.dataset.state=state;
 const dot=el('span',undefined,'conn-dot');dot.setAttribute('aria-hidden','true');
 pill.append(dot,el('span',connectionStateNames[state]||state));return pill;
}
function scrollToSection(id){const target=document.getElementById(id);if(target){target.scrollIntoView({behavior:'smooth',block:'start'});target.querySelector('button, summary')?.focus({preventScroll:true});}}
async function refreshConnections(){
 try{connectionsReport=await api('connections');connectionsError='';}
 catch(e){connectionsError=e.message;}
 drawConnections();
}
function gmailCheckPending(report){const c=(report?.connections||[]).find(x=>x.id==='gmail');return ['requested','checking'].includes(c?.check?.state);}
// Polls only the read-only report while an owner-requested check is outstanding.
function watchGmailCheck(){
 clearInterval(connectionsPoll);let rounds=0;
 connectionsPoll=setInterval(async()=>{
  if(page!=='settings'||++rounds>60){clearInterval(connectionsPoll);return;}
  await refreshConnections();
  if(!gmailCheckPending(connectionsReport))clearInterval(connectionsPoll);
 },3000);
}
function connectionBody(c){
 const body=el('div',undefined,'conn-body'),actions=el('div',undefined,'actions conn-actions');
 if(c.id==='model'){
  body.append(connectionFacts([['供应商',c.provider],['模型',c.model],['请求地址',c.endpoint],['最近核实',c.checked_at&&connectionTime(c.checked_at)]]));
  if(c.state==='unverified')body.append(el('p','只有真实回复才算验证；获取模型列表不代表可以聊天。','muted'));
  actions.append(action('管理供应商',()=>scrollToSection('model-providers'),true));
  if(c.state==='unverified')actions.append(action('打开聊天',()=>go('chat'),true));
 }
 if(c.id==='gmail'){
  body.append(connectionFacts([['账号',c.account],['最近核实',c.checked_at&&connectionTime(c.checked_at)]]));
  const note=gmailCheckNotes[c.check?.state];
  if(note){const p=el('p',note,'muted conn-check-note');p.setAttribute('role','status');body.append(p);}
  if(!c.verifiable)body.append(el('p','在电脑运行 tools/gmail_login.py 完成 OAuth 授权，把生成的文件位置写入私人目录 local.json 的 gmail_credentials，然后重启服务。','muted'));
  else{
   const pending=['requested','checking'].includes(c.check?.state);
   const verify=action(pending?'正在验证…':'验证连接',async()=>{
    const check=await api('connections/gmail/check',{});
    connectionsReport={...connectionsReport,connections:connectionsReport.connections.map(x=>x.id==='gmail'?{...x,check}:x)};
    drawConnections();watchGmailCheck();
   },true);
   verify.disabled=pending;verify.title='读取一次 Gmail 账号资料，不发送邮件';actions.append(verify);
  }
 }
 if(c.id==='qq'){
  body.append(connectionFacts([['已授权群',`${c.active_groups} 个`],['最近收到消息',c.last_message_at&&connectionTime(c.last_message_at)]]));
  const groups=state.groups||[];
  if(groups.length){
   const list=el('div',undefined,'conn-list');
   for(const g of groups){
    const row=el('div',undefined,'conn-list-row'),text=el('div',undefined,'conn-list-text');
    text.append(el('span',g.title,'conn-list-title'),el('span',`群号 ${g.id} · ${g.active?'允许新增采集':'已停止新增采集，旧资料仍可用'}`,'muted'));
    row.append(text,action(g.active?'撤销新增采集':'重新授权',async()=>{await api('groups',{...g,active:!g.active});await sync();},true));list.append(row);
   }
   body.append(list);
  }
  body.append(el('p','群消息由 OneBot 连接器（如 NapCat）推送到本服务，连接器需使用私人目录中的 qq.token。','muted'));
  actions.append(action('授权 QQ 群',()=>form('持续读取指定群，并允许按任务需要使用云端模型',[{name:'id',label:'群号'},{name:'title',label:'群名称'}],v=>api('groups',{...v,active:true})),true));
 }
 if(c.id==='phone'){
  const t=c.transport||{};
  body.append(connectionFacts([['监听',`${t.listen_host==='127.0.0.1'?'仅本机':t.listen_host} · 端口 ${t.port}`],['传输',t.tls?'HTTPS':'HTTP（仅本机）']]));
  if(c.devices?.length){
   const list=el('div',undefined,'conn-list');
   for(const d of c.devices){
    const row=el('div',undefined,'conn-list-row'),text=el('div',undefined,'conn-list-text');
    const seen=d.last_seen_at?`最近连接 ${connectionTime(d.last_seen_at)}${d.tailnet?' · Tailscale':''}`:'本次启动后未连接';
    text.append(el('span',d.name,'conn-list-title'),el('span',d.active?`已授权 · ${seen}`:'已撤销','muted'));
    row.append(text);
    if(d.active)row.append(action('撤销设备',async()=>{if(confirm(`撤销「${d.name}」？该手机需重新配对才能连接。`)){await api(`devices/${d.id}/revoke`,{});await sync();}},true));
    list.append(row);
   }
   body.append(list);
  }
  actions.append(action('配对新手机',pairPhone,true),action('查看连接步骤',()=>scrollToSection('phone-setup'),true));
 }
 if(c.id==='worker'){
  body.append(connectionFacts([['最近活动',c.checked_at&&connectionTime(c.checked_at)]]));
  body.append(el('p','由工作区启动程序运行并在退出后重启；结果未知的外部操作不会被重放。','muted'));
 }
 if(actions.childElementCount)body.append(actions);
 return body;
}
function connectionRow(c){
 const meta=connectionMeta[c.id]||{title:c.id,icon:'plug'};
 const row=el('details',undefined,'conn-row');row.id='connection-'+c.id;row.dataset.state=c.state;row.open=openConnections.has(c.id);
 row.addEventListener('toggle',()=>{if(row.open)openConnections.add(c.id);else openConnections.delete(c.id);});
 const summary=el('summary',undefined,'conn-summary');
 const badge=el('span',undefined,'conn-icon');badge.append(icon(meta.icon));
 const text=el('span',undefined,'conn-text');text.append(el('span',meta.title,'conn-title'),el('span',c.detail,'conn-detail'));
 const chevron=icon('chevron-right');chevron.classList.add('conn-chevron');
 summary.append(badge,text,connectionStatePill(c.state),chevron);
 row.append(summary,connectionBody(c));return row;
}
function drawConnections(){
 const section=$('#connections');if(!section)return;
 const list=section.querySelector('.conn-group'),status=section.querySelector('.conn-overview'),foot=section.querySelector('.conn-footnote');
 // Re-reading an unchanged report must not replace rows the user has open or focused.
 const drawn=JSON.stringify([connectionsError,connectionsReport?.connections,(state.groups||[]).map(g=>[g.id,g.active,g.title])]);
 if(section._drawn===drawn)return;
 section._drawn=drawn;
 list.replaceChildren();
 if(connectionsError){const p=el('p','未取得连接状态：'+connectionsError,'form-error conn-empty');p.setAttribute('role','alert');list.append(p);}
 const items=connectionsReport?.connections||[];
 if(!items.length&&!connectionsError)list.append(el('p',token?'正在读取连接状态…':'先连接电脑工作区，再查看连接状态。','muted conn-empty'));
 for(const c of items)list.append(connectionRow(c));
 const counts={};for(const c of items)counts[c.state]=(counts[c.state]||0)+1;
 status.textContent=items.length?['verified','unverified','not_configured','error'].filter(s=>counts[s]).map(s=>`${counts[s]} 项${connectionStateNames[s]}`).join(' · '):'';
 foot.textContent=connectionsReport?`在线状态从本次服务启动（${connectionTime(connectionsReport.service_started_at)}）开始记录；已验证表示有真实往返证据，未验证表示已配置但尚无证据。`:'';
 paintIcons();
}
function connectionsPanel(box){
 const section=el('section',undefined,'settings-section conn-section');section.id='connections';
 const head=el('div',undefined,'conn-head'),titles=el('div');
 titles.append(el('h2','连接状态'),el('p','','muted conn-overview'));
 const reload=action('',refreshConnections,true);reload.classList.add('icon-button');reload.setAttribute('aria-label','重新读取连接状态');reload.title='重新读取连接状态';reload.append(icon('refresh-cw'));
 head.append(titles,reload);
 const list=el('div',undefined,'conn-group');list.setAttribute('aria-label','连接列表');
 section.append(head,list,el('p','','muted conn-footnote'));box.append(section);
 drawConnections();
 if(token)refreshConnections();
}
function workspacePanel(box){
 const section=el('section',undefined,'settings-section');section.append(el('h2','工作区'));
 const actions=el('div',undefined,'actions');
 actions.append(action('创建数据库备份',async()=>{const r=await api('backup',{});$('#notice').textContent='备份已创建：'+r.path;},true),action('退出工作区',()=>{sessionStorage.removeItem('pa.token');token='';connectionsReport=null;location.reload();},true));
 section.append(actions);box.append(section);
}
function renderSettings(box){
 connectionsPanel(box);providersPanel(box);setupPanel(box);workspacePanel(box);
}
