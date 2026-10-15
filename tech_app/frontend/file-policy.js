(function(global){
  'use strict';
  let policy=null;
  function expand(items){return [...new Set(items.flatMap(item=>policy.groups[item] || [item]))];}
  function extension(name){return (String(name || '').toLowerCase().match(/\.[^.\/\\]+$/) || [''])[0];}
  function accept(purpose){return expand(policy.purposes[purpose].accept).join(',');}
  function capability(purpose,name){
    const rule=policy.purposes[purpose],ext=extension(name);
    const mode=!expand(rule.accept).includes(ext)?'unsupported':expand(rule.direct).includes(ext)?'direct':expand(rule.converted).includes(ext)?'converted':'save_only';
    return {mode,label:policy.labels[mode],note:policy.notes[ext] || ''};
  }
  function validate(file,purpose){
    if(!policy)return '文件能力配置尚未加载，请稍后重试';
    if(capability(purpose,file.name).mode==='unsupported')return '此入口不支持 '+file.name;
    if(!file.size)return '文件为空：'+file.name;
    if(file.size>policy.max_bytes)return '单文件上限16MiB：'+file.name;
    return '';
  }
  function describe(purpose,name){const c=capability(purpose,name);return c.label+(c.note?' · '+c.note:'');}
  function purposeOf(input){
    if(input.dataset.filePurpose)return input.dataset.filePurpose;
    const id=input.id;
    if(id==='fileInput')return document.getElementById('btnParse')?'drawing':'quote_text';
    if(['chatFileInput','pjFileInput'].includes(id))return 'quote_text';
    if(['techDrawInput','modelFile','ocChatFileInput','ttModel','ttDocs','attachInput'].includes(id))return 'asset';
    if(['techReqInput','documentFiles','file_bom_assembly','file_technical_spec'].includes(id))return 'requirement';
    if(id==='file_drawing_2d')return 'drawing';
    if(id==='file_model_3d')return 'model_upload';
    if(id==='file3d')return 'model_import';
    if(input.hasAttribute('data-qq-parse-input'))return 'quick';
    return null; // 知识库工作簿等专用业务导入不改白名单。
  }
  function enhance(){
    const host=document.querySelector('.unified-input-card') || document.querySelector('.chat-input-wrapper')?.parentElement || document.querySelector('.tech-chat-pane') || document.querySelector('.upload-form');
    if(host && !document.getElementById('filePolicySummary')){
      const summary=document.createElement('p');summary.id='filePolicySummary';summary.className='file-policy-hint';summary.style.cssText='font-size:12px;color:var(--text-secondary,#64748b);line-height:1.5;margin:6px 0';
      summary.textContent='单文件≤16MiB。可直接解析 / 需转换后解析 / 仅保存并交接，按用途区分；报价图片与旧DOC/XLS仅保存，包装图纸优先DWG/DXF。';host.appendChild(summary);
    }
    document.querySelectorAll('input[type="file"]').forEach(input=>{
      const purpose=purposeOf(input);if(!purpose || input.dataset.filePolicyApplied)return;
      input.dataset.filePolicyApplied='1';input.dataset.filePurpose=purpose;
      if(policy)input.accept=accept(purpose);
      input.title='单文件≤16MiB；'+accept(purpose);
      input.addEventListener('change',()=>{
        input.title=Array.from(input.files || []).map(file=>file.name+'：'+describe(purpose,file.name)).join('；') || '单文件≤16MiB；原文件随项目交接。';
        const summary=document.getElementById('filePolicySummary');if(summary && input.files.length)summary.textContent=input.title;
      });
    });
  }
  const ready=global.fetch('/file-policy.json').then(r=>{if(!r.ok)throw Error('文件能力配置读取失败');return r.json();}).then(data=>{policy=data;return data;});
  global.CPQFilePolicy={ready,accept,capability,validate,describe,enhance};
  if(typeof document!=='undefined'){
    ready.then(()=>{
      const start=()=>{enhance();new MutationObserver(enhance).observe(document.body,{childList:true,subtree:true});};
      if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
      document.addEventListener('change',event=>{
        const input=event.target;if(!input.matches || !input.matches('input[type="file"]'))return;
        const purpose=purposeOf(input);if(!purpose)return;
        const error=Array.from(input.files || []).map(file=>validate(file,purpose)).find(Boolean);
        if(error){input.value='';event.stopImmediatePropagation();global.alert(error);}
      },true);
    }).catch(error=>{
      const report=()=>{document.querySelectorAll('input[type="file"]').forEach(input=>{if(purposeOf(input)){input.disabled=true;input.title=error.message+'，请刷新重试';}});};
      if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',report);else report();
    });
  }
})(typeof window!=='undefined'?window:globalThis);
