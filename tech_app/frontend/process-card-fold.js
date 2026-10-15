/* 与普通助手气泡同款：仅折叠长执行列表，不删日志、不新建卡片或追加摘要。 */
(function(global){
  'use strict';
  if(global.CPQProcessCardFold)return;
  let sequence=0;
  function sync(host){
    if(!host || !host.parentNode)return;
    const rows=Array.from(host.children).filter(node=>node.getAttribute('data-agent-role')==='tool-item'||node.classList.contains('oc-process-step'));
    let toggle=host.__cpqOverflowToggle;
    if(rows.length<=5){
      host.removeAttribute('data-process-folded');
      if(toggle){toggle.remove();host.__cpqOverflowToggle=null;}
      return;
    }
    if(!toggle){
      toggle=document.createElement('button');toggle.type='button';toggle.className='oc-process-toggle oc-process-overflow-toggle';
      toggle.setAttribute('data-process-overflow-toggle','');
      if(!host.id)host.id='process-steps-'+(++sequence);
      toggle.setAttribute('aria-controls',host.id);
      toggle.addEventListener('click',()=>{host.dataset.processExpanded=host.dataset.processExpanded==='true'?'false':'true';sync(host);});
      host.parentNode.insertBefore(toggle,host.nextSibling);host.__cpqOverflowToggle=toggle;
    }
    const expanded=host.dataset.processExpanded==='true';
    host.dataset.processFolded=expanded?'false':'true';
    toggle.setAttribute('aria-expanded',expanded?'true':'false');
    const label=expanded?'收起执行步骤':'展开其余 '+(rows.length-5)+' 个执行步骤';
    if(toggle.textContent!==label)toggle.textContent=label;
  }
  function refresh(){document.querySelectorAll('.oc-task-steps,.oc-process-steps').forEach(sync);}
  global.CPQProcessCardFold={sync,refresh};
  function start(){
    refresh();
    new MutationObserver(records=>{
      const hosts=new Set();
      for(const record of records){
        const parent=record.target.closest?.('.oc-task-steps,.oc-process-steps');if(parent)hosts.add(parent);
        for(const node of record.addedNodes){
          if(node.nodeType!==1)continue;
          if(node.matches('.oc-task-steps,.oc-process-steps'))hosts.add(node);
          node.querySelectorAll('.oc-task-steps,.oc-process-steps').forEach(host=>hosts.add(host));
        }
      }
      hosts.forEach(sync);
    }).observe(document.body,{childList:true,subtree:true});
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
})(typeof window!=='undefined'?window:globalThis);
