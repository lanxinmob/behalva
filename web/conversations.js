'use strict';
let conversationId='', chatInputs={}, pendingMessages={},preserveChatScrollTop=null;
function go(target){
 if(target!=='tasks')selectedTaskId=null;
 if(target!=='operations')selectedOperationId=null;
 page=target;render();paintIcons();
}
function freshConversation(){conversationId=crypto.randomUUID().replaceAll('-','');go('chat');}
function guide(box){
 const intro=el('section',undefined,'welcome');intro.append(el('h2','从这里开始'),el('p','完成连接与模型配置，就可以开始第一段对话。'));
 intro.append(action('开始聊天',()=>go('chat')));box.append(intro);
 for(const [title,body,target,label] of [
  ['1 · 连接工作区','电脑先用私人工作区的 owner.token 登录。手机推荐通过 Tailscale 连接电脑：在“连接与授权”按步骤取得 HTTPS 地址，点“配对新手机”生成连接配置，再在手机粘贴导入。可信证书无需手填指纹；自签名证书由启动程序自动生成。','settings','连接与设备'],
  ['2 · 接入模型',state.model_configured?'已有模型配置；完成一次真实回复后才能确认可用。':'在电脑“连接与授权 → 模型供应商”点击“添加供应商”，填写名称、API Key 和请求地址，获取并选择模型后保存。','settings','管理模型供应商'],
  ['3 · 准备资料','导入或索引资料后，在“资料”批准需要交给云端模型的范围。聊天发送的文字和该对话历史会交给模型；文件不会因进入索引就自动外发。','sources','管理资料'],
  ['4 · 聊天并发起任务','在聊天中讨论目标，明确后点“创建调查任务”，核对目标、链接和资料。想改进助手时点“保存功能需求”。任务完成后在草稿、表格查看结果。','chat','打开聊天']]){
  const c=el('section',undefined,'guide-step');c.append(el('h3',title),el('div',body,'body'));c.append(action(label,()=>go(target),true));box.append(c);
 }
 box.append(card('离线与确认','手机离线时可以看已同步聊天、保存待发送消息和草稿，重连后同步。发送邮件、提交表单与新代码启用仍需单独核对确认。'));
}
function conversationTask(c,kind){
 const feature=kind==='feature';
 let sequence=c.sequence,request=null;
 form(feature?'保存待开发的功能需求':'从对话创建调查任务',[
  {name:'title',label:feature?'功能目标与验收要求':'任务目标',multiline:true},
  ...feature?[]:[{name:'urls',label:'相关链接（每行一个，可选）',multiline:true},
    {name:'sources',label:'关联资料（云端使用仍需授权）',options:state.sources||[]}]
 ],async v=>{
  const fields={title:v.title,kind,expected_sequence:sequence,urls:(v.urls||'').split('\n').filter(Boolean),source_ids:v.sources||[]};
  if(request&&JSON.stringify({...request,request_id:undefined})!==JSON.stringify(fields))throw new Error('上次提交尚未确认送达，请先恢复原表单内容并保存以查询回执。');
  request=request||{...fields,request_id:crypto.randomUUID()};
  try{await api(`conversations/${c.id}/tasks`,request);}catch(e){
   if(e.status&&e.status<500)request=null;
   if(e.status===409){
    if(!$('#refresh-conversation-context')){
     const refresh=action('查看最新对话并更新任务依据',async()=>{
      state=await api('state');const latest=state.conversations.find(x=>x.id===c.id);
      sequence=latest.sequence;
      $('#editor-fields').append(card('确认本次任务将附带的最新对话',(state.messages||[])
       .filter(m=>m.conversation_id===c.id).sort((a,b)=>a.sequence-b.sequence)
       .map(m=>(m.role==='user'?'你':'助手')+'：'+m.body).join('\n\n')));
      $('#editor-error').textContent='任务依据已更新，请核对上方对话和表单后再次保存。';
     },true);refresh.id='refresh-conversation-context';$('#editor-fields').append(refresh);
    }
   }throw e;
  }
  page='tasks';
 });
}
function renderChatMessage(m){
 const text=formatDisplayText(m.body);
 const row=el('article',undefined,`message ${m.role}`);
 const label=m.role==='user'?'你':'助手';
 row.append(el('header',label,'message-label'));
 row.append(el('div',text,'body message-body'));
 if(m.role==='assistant'&&!['completed','stop'].includes(m.completion_status))row.append(el('p','回复可能不完整，可继续追问。','muted'));
 return row;
}
function conversationPreview(item){
 const last=(state.messages||[]).filter(x=>x.conversation_id===item.id).sort((a,b)=>b.sequence-a.sequence)[0];
 if(!last)return '暂无消息';
 return formatDisplayText(last.body).replace(/\s+/g,' ').slice(0,72)||'暂无消息';
}
function renderChat(box){
 const conversations=state.conversations||[];
 if(!conversationId)conversationId=conversations[0]?.id||crypto.randomUUID().replaceAll('-','');
 const c=conversations.find(x=>x.id===conversationId);
 const layout=el('div',undefined,'chat-layout');
 const rail=el('aside',undefined,'chat-rail');
 rail.append(el('div','对话','chat-rail-title'),action('＋ 新对话',freshConversation,true));
 const list=el('div',undefined,'conversation-list');
 for(const item of conversations){
  const btn=action(item.title.slice(0,48)||'未命名对话',()=>{conversationId=item.id;render();},true);
  btn.className='conversation-item'+(item.id===conversationId?' selected':'');
  btn.append(el('span',conversationPreview(item),'conversation-snippet muted'));
  if(item.id===conversationId)btn.setAttribute('aria-current','true');
  list.append(btn);
 }
 if(!conversations.length)list.append(el('p','开始你的第一段对话。','muted'));
 rail.append(list);
 const select=el('select');select.className='sr-only chat-select-fallback';select.setAttribute('aria-label','选择对话');
 if(!c){const o=el('option','新对话');o.value=conversationId;select.append(o);}
 for(const item of conversations){const o=el('option',item.title.slice(0,55));o.value=item.id;select.append(o);}
 select.value=conversationId;select.onchange=()=>{conversationId=select.value;render();};
 rail.append(select);
 const main=el('div',undefined,'chat-main');
 const mobilePick=el('div',undefined,'chat-mobile-picker');
 const mobileSelect=select.cloneNode(true);mobileSelect.classList.remove('sr-only');mobileSelect.classList.add('chat-mobile-select');
 mobileSelect.onchange=()=>{conversationId=mobileSelect.value;render();};
 const freshMobile=action('＋ 新对话',freshConversation,true);
 freshMobile.classList.add('chat-mobile-new');
 mobilePick.append(mobileSelect,freshMobile);main.append(mobilePick);
 const scroll=el('div',undefined,'chat-scroll');scroll.tabIndex=0;scroll.setAttribute('aria-label','对话内容');
 const messages=(state.messages||[]).filter(m=>m.conversation_id===conversationId).sort((a,b)=>a.sequence-b.sequence);
 const log=el('div',undefined,'chat-log');log.setAttribute('role','log');log.setAttribute('aria-label','聊天记录');
 if(!state.model_configured){const note=el('div',undefined,'setup-strip');note.append(el('p','模型尚未接入。消息会保存，当前不会产生 AI 回复。'),action('接入模型 API',()=>go('settings'),true));log.prepend(note);}
 for(const m of messages)log.append(renderChatMessage(m));
 if(!messages.length){const start=el('div',undefined,'empty chat-start');start.append(icon('message-circle'),el('h2','有什么想一起推进？'),el('p','说说你的目标，或聊聊希望助手增加的能力。'));const prompts=el('div',undefined,'chat-prompts');for(const [label,text] of [['整理一份材料','帮我梳理这件事需要哪些材料。'],['讨论一个新功能','我希望助手增加一个新功能，我们先讨论需求。']])prompts.append(action(label,()=>{chatInputs[conversationId]=text;input.value=text;input.focus();},true));start.append(prompts);log.append(start);}
 scroll.append(log);
 if(preserveChatScrollTop!=null){scroll.scrollTop=preserveChatScrollTop;preserveChatScrollTop=null;}
 main.append(scroll);
 const task=(state.tasks||[]).find(t=>t.id===c?.last_task_id);
 const busy=task&&task.state!=='completed';
 if(busy){const note=el('div',undefined,'chat-status');note.setAttribute('role','status');note.append(el('h3',statusNames[task.state]||task.state),el('div',task.result?.message||task.notice||'消息已保存，等待助手回复。','body'));
  if(!['running','queued'].includes(task.state)){
   note.append(action('继续原消息',async()=>{await api(`tasks/${task.id}/resume`,{reply:'',request_id:crypto.randomUUID()});await sync();},true));
   note.append(action('查看调用与恢复详情',()=>go('tasks'),true));
  }main.append(note);
 }
 const input=el('textarea');input.id='chat-input';input.setAttribute('aria-label','消息');input.placeholder='输入消息…';
 input.value=chatInputs[conversationId]||'';input.oninput=()=>{chatInputs[conversationId]=input.value;};
 input.addEventListener('compositionstart',()=>{input.dataset.composing='true';});
 input.addEventListener('compositionend',()=>{delete input.dataset.composing;});
 const send=action('发送',async()=>{
  const id=conversationId,original=input.value,body=original.trim();if(!body)throw new Error('请先输入消息。');
  let request=pendingMessages[id];
  if(request&&request.body!==body)throw new Error('上一条消息尚未确认送达，请先恢复原内容并重试或同步查看。');
  request=request||{conversation_id:id,body,expected_sequence:c?.sequence||0,request_id:crypto.randomUUID()};
  pendingMessages[id]=request;
  try{await api('messages',request);}catch(e){if(e.status&&e.status<500)delete pendingMessages[id];throw e;}
  delete pendingMessages[id];if(chatInputs[id]===original)chatInputs[id]='';await sync();
 });send.disabled=!!busy;
 input.addEventListener('keydown',e=>{
  if(e.key!=='Enter'||e.shiftKey||e.altKey||e.isComposing||input.dataset.composing==='true'||e.keyCode===229)return;
  if(!e.ctrlKey&&!e.metaKey&&matchMedia('(pointer: coarse)').matches)return;
  e.preventDefault();
  if(e.repeat||send.disabled||!input.value.trim())return;
  send.click();
 });
 send.setAttribute('aria-keyshortcuts','Enter Control+Enter Meta+Enter');
 const composer=el('div',undefined,'composer'),inner=el('div',undefined,'composer-inner'),row=el('div',undefined,'composer-row');send.replaceChildren(icon('arrow-up'));send.classList.add('icon-button');send.setAttribute('aria-label','发送');send.title=busy?'等待原消息处理完成':'发送';row.append(input,send);inner.append(row);composer.append(inner);main.append(composer);
 if(c){const actions=el('div',undefined,'actions composer-actions');actions.append(
  action('创建调查任务',()=>conversationTask(c,'research'),true),
  action('保存功能需求',()=>conversationTask(c,'feature'),true));inner.append(actions);
 }
 const privacy=el('details');privacy.append(el('summary','云端处理与任务说明'),el('p','发送的文字及本对话历史会交给配置的云端模型。联网调查请创建任务。创建任务时附带当前对话；功能需求仅保存到待开发列表，不会直接修改或启用程序。','muted'));inner.append(privacy);
 const inspector=el('aside',undefined,'chat-inspector mac-split-pane');
 if(c){
  const related=(state.tasks||[]).filter(t=>t.conversation_id===c.id&&t.kind!=='chat');
  inspector.append(el('h3','关联任务','inspector-title'));
  if(related.length){for(const t of related)inspector.append(showTask(t,{compact:true}));}
  else inspector.append(el('p','暂无关联任务。','muted'));
 }else inspector.append(el('p','选择或开始对话。','muted'));
 const split=el('div',undefined,'mac-split chat-split');
 const listPane=el('div',undefined,'mac-split-pane mac-split-list');listPane.append(rail);
 const mainPane=el('div',undefined,'mac-split-pane mac-split-main');mainPane.append(main);
 split.append(listPane,mainPane,inspector);
 layout.append(split);box.append(layout);
}
// Poll only the active chat, preserving typed text/focus and reading no other workspace.
setInterval(async()=>{
 if(typeof page==='undefined'||page!=='chat'||!token||document.hidden||$('#editor').open)return;
 try{const next=await api('state');if(next.cursor===state.cursor)return;
  const input=$('#chat-input');if(input?.dataset.composing==='true')return;
  const focused=document.activeElement===input,start=input?.selectionStart,end=input?.selectionEnd;
  const scroller=$('.chat-scroll'),scroll=scroller?.scrollTop||0;
  const maxScroll=scroller?Math.max(0,scroller.scrollHeight-scroller.clientHeight):0;
  const atEnd=scroller&&maxScroll>0&&maxScroll-scroll<50;
  preserveChatScrollTop=page==='chat'&&!atEnd?scroll:null;state=next;render();paintIcons();
  if(focused){const replacement=$('#chat-input');replacement.focus({preventScroll:true});replacement.setSelectionRange(start,end);}
  const restore=()=>{const replacement=$('.chat-scroll');if(replacement&&!atEnd)replacement.scrollTop=scroll;};
  requestAnimationFrame(()=>requestAnimationFrame(restore));
 }catch(e){$('#notice').textContent='暂时无法同步聊天：'+e.message;}
},4000);
