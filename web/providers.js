'use strict';
function providerPricingMode(p){return p.pricing_mode||(typeof p.input_usd_per_million==='number'&&typeof p.output_usd_per_million==='number'?'custom':'estimate');}
function providerButton(name,description,fn){
 const b=action('',fn,true);b.classList.add('icon-button');b.setAttribute('aria-label',description);b.title=description;b.append(icon(name));return b;
}
function providersPanel(box){
 const section=el('section',undefined,'settings-section');section.id='model-providers';
 const heading=el('div',undefined,'row');heading.append(el('h2','模型供应商'));
 heading.append(action('添加供应商',()=>editProvider(null)));section.append(heading);
 const list=el('div');section.append(list);box.append(section);
 if(!token){list.append(el('p','先连接电脑工作区，再添加模型供应商。','muted'));return;}
 api('providers').then(result=>{
  if(!section.isConnected)return;
  if(!result.providers.length)list.append(el('p',state.model_configured?'当前使用启动时的模型配置；添加供应商后可在这里切换。':'尚未添加供应商。','muted'));
  for(const p of result.providers){
   const row=el('article',undefined,'provider-row'),content=el('div',undefined,'provider-info');
   content.append(el('h3',p.name),el('div',p.base_url,'uri'),el('div',p.model||'尚未选择模型','muted'));
   if(p.credential_unavailable)content.append(el('p','原系统密钥无法恢复，请编辑并重新填写 API Key。','form-error'));
   if(p.base_url.startsWith('http://'))content.append(el('p','HTTP：API Key 和资料会明文传输。','muted'));
   row.append(content);const actions=el('div',undefined,'provider-actions');
   if(p.active)actions.append(el('span','使用中','badge'));
   else actions.append(providerButton('play','使用 '+p.name,async()=>{await api('providers/activate',{id:p.id,expected_revision:p.revision});await sync();}));
   actions.append(providerButton('pencil','编辑 '+p.name,()=>editProvider(p)));row.append(actions);
   content.append(el('div',providerPricingMode(p)==='custom'?'费用按手动报价计算':'费用自动估算 · 非实际账单','muted'));
   if(p.active&&!p.model)content.append(el('p','选择模型后可开始聊天。','form-error'));
   list.append(row);
  }
  paintIcons();
 }).catch(e=>{if(section.isConnected){heading.lastElementChild.disabled=true;list.append(el('p',e.status===403?'请在电脑用管理令牌添加供应商；手机不保存模型密钥。':e.message,'muted'));}});
}
function editProvider(provider){
 const p=provider||{},dialog=el('dialog');dialog.id='provider-editor';
 const draftId=p.id||crypto.randomUUID().replaceAll('-','');let saving=false;
 dialog.append(el('h2',provider?'编辑供应商':'添加供应商'));
 const controls={};
 function input(key,label,value='',type='text',parent=dialog){
  const l=el('label',label),i=el('input');i.type=type;i.value=value;i.setAttribute('aria-label',label);
  i.autocomplete='off';l.append(i);parent.append(l);controls[key]=i;return i;
 }
 input('name','名称',p.name||'');
 const key=input('api_key','API Key','','password');key.placeholder=p.credential_unavailable?'需要重新填写 API Key':provider?'已保存，留空保持原密钥':'服务商提供的密钥';
 input('base_url','API 请求地址',p.base_url||'https://api.openai.com/v1','url');
 const httpNotice=el('p','HTTP：API Key 和资料会明文传输，建议使用 HTTPS。','muted');
 function transportHint(){httpNotice.hidden=!controls.base_url.value.trim().startsWith('http://');}
 controls.base_url.addEventListener('input',transportHint);transportHint();dialog.append(httpNotice);
 const modelRow=el('div',undefined,'row');modelRow.append(el('h3','模型'));const models=el('datalist');models.id='provider-model-options';
 const model=input('model','模型 ID',p.model||'');model.setAttribute('list',models.id);dialog.append(models);
 const error=el('p',undefined,'form-error');error.setAttribute('role','alert');
 const status=el('p',undefined,'muted');status.setAttribute('role','status');
 const fetchModels=action('获取可用模型',async()=>{
  error.textContent='';status.textContent='正在获取模型列表…';
  try{
   const address=controls.base_url.value.trim(),secret=key.value;
   const result=await api('providers/models',{id:p.id,base_url:address,api_key:secret,protocol:controls.protocol.value});
   if(!dialog.isConnected)return;
   if(address!==controls.base_url.value.trim()||secret!==key.value){status.textContent='地址或密钥已变化，请重新获取模型。';return;}
   models.replaceChildren();for(const id of result.models){const o=el('option');o.value=id;models.append(o);}
   if(!model.value&&result.models.length===1){model.value=result.models[0];model.dispatchEvent(new Event('input'));}
   status.textContent=result.models.length?`已取得 ${result.models.length} 个模型，点击模型输入框选择。`:'服务未列出模型；请填写服务商提供的模型 ID。';
  }catch(e){status.textContent='';error.textContent=e.message;}
 },true);modelRow.append(fetchModels);dialog.insertBefore(modelRow,model.parentElement);
 dialog.append(status);
 const advanced=el('details');
 advanced.append(el('summary','高级设置（可选）'));
 const label=el('label','接口类型'),protocol=el('select');protocol.setAttribute('aria-label','接口类型');
 for(const [value,text] of [['responses','OpenAI Responses'],['chat_completions','OpenAI Chat Completions']]){const o=el('option',text);o.value=value;protocol.append(o);}protocol.value=p.protocol||'responses';label.append(protocol);advanced.append(label);controls.protocol=protocol;
 const feeLabel=el('label','费用计算'),feeMode=el('select');feeMode.setAttribute('aria-label','费用计算');
 for(const [value,text] of [['estimate','自动估算（默认）'],['custom','手动报价']]){const o=el('option',text);o.value=value;feeMode.append(o);}
 feeMode.value=providerPricingMode(p);feeLabel.append(feeMode);advanced.append(feeLabel);controls.pricing_mode=feeMode;
 const custom=el('div');advanced.append(custom);
 custom.append(el('p','按服务商报价填写，单位：美元 / 百万 token。','muted'));
 for(const [field,text] of [['input_usd_per_million','输入单价'],['output_usd_per_million','输出单价']]){const i=input(field,text,p[field]??'','number',custom);i.min='0';i.step='any';}
 const estimate=el('p','默认按输入 $10、输出 $50 / 百万 token 估算预算；这不是服务商报价，实际账单可能不同。','muted');advanced.append(estimate);
 function resetPricing(){feeMode.value='estimate';controls.input_usd_per_million.value='';controls.output_usd_per_million.value='';pricingHint();}
 let pricedModel=model.value,pricedAddress=controls.base_url.value.trim();
 model.addEventListener('input',()=>{if(model.value!==pricedModel){resetPricing();status.textContent='模型已变化，费用恢复自动估算。';pricedModel=model.value;}});
 controls.base_url.addEventListener('input',()=>{if(controls.base_url.value.trim()!==pricedAddress){resetPricing();pricedAddress=controls.base_url.value.trim();}});
 for(const field of ['base_url','api_key'])controls[field].addEventListener('input',()=>{models.replaceChildren();status.textContent='';});
 const hint=el('p',undefined,'muted');hint.setAttribute('role','status');
 function pricingHint(){custom.hidden=feeMode.value!=='custom';estimate.hidden=feeMode.value!=='estimate';hint.textContent=!model.value.trim()?'选择模型后即可开始聊天。':feeMode.value==='estimate'?'费用自动估算，实际账单以服务商为准。':(controls.input_usd_per_million.value===''||controls.output_usd_per_million.value===''?'请补齐手动报价，或切回自动估算。':(p.active?'保存后对下一次模型调用生效。':''));}
 feeMode.addEventListener('change',pricingHint);
 for(const field of ['model','input_usd_per_million','output_usd_per_million'])controls[field].addEventListener('input',pricingHint);
 pricingHint();dialog.append(advanced,hint,error);
 const buttons=el('div',undefined,'actions');
 function values(activate){
  const result={id:draftId,expected_revision:p.revision||0,activate};
  for(const [k,c] of Object.entries(controls))result[k]=k==='api_key'?c.value:c.value.trim();
  for(const k of ['input_usd_per_million','output_usd_per_million']){result[k]=result.pricing_mode==='estimate'||result[k]===''?null:Number(result[k]);if(result[k]!==null&&(!Number.isFinite(result[k])||result[k]<0))throw new Error('单价需要填写非负数。');}
  if(result.pricing_mode==='custom'&&(result.input_usd_per_million===null||result.output_usd_per_million===null))throw new Error('请补齐手动报价，或使用自动估算。');
  if(!result.name)throw new Error('请填写名称。');if(!result.base_url)throw new Error('请填写 API 请求地址。');return result;
 }
 async function save(activate){
  if(saving)return;
  saving=true;const buttons=Array.from(dialog.querySelectorAll('button'));for(const b of buttons)b.disabled=true;
  error.textContent='';try{await api('providers',values(activate));dialog.close();await sync();}catch(e){error.textContent=e.message;}
  finally{saving=false;for(const b of buttons)b.disabled=false;}
 }
 buttons.append(action('保存并使用',()=>save(true)),action('仅保存',()=>save(false),true),action('取消',()=>dialog.close(),true));dialog.append(buttons);
 dialog.onclose=()=>{key.value='';dialog.remove();};document.body.append(dialog);dialog.showModal();controls.name.focus();
}
