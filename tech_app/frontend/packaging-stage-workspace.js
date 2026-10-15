/* 包装排模与组装两页签；数据来自只读workspace投影，不调用模型或计算成本。 */
(() => {
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const size=v=>Array.isArray(v)&&v.length===2&&v.every(n=>Number.isFinite(Number(n))&&Number(n)>0)
    ? `${Number(v[0]).toFixed(2)} × ${Number(v[1]).toFixed(2)} mm` : '尺寸未取得';
  const preview=(board,code,label='原图预览 / 候选切换')=>`<button type="button" class="inline-action" data-pkg-drawing="${esc(code||'')}">${esc(label)}</button>`;
  function parts(board) {
    let html='<section class="inline-card"><div class="inline-card-title">3.1 方案与部件</div>';
    html+=`<div class="inline-row">当前方案：${esc(board.selected_scheme_id||((board.schemes||[]).length>1?'尚未选定':'当前图纸'))} ${preview(board,'','查看当前原图')}</div>`;
    html+=(board.schemes||[]).map(s=>`<button class="inline-action" type="button" data-pkg-scheme="${esc(s.scheme_id||s.id)}" aria-pressed="${String((s.scheme_id||s.id)===board.selected_scheme_id)}">${esc(s.name||s.label||'方案 '+(s.scheme_id||s.id))}</button>`).join('');
    if(!(board.parts||[]).length)html+='<div class="inline-empty">尚无当前方案的业务部件，请先解析图纸或选择方案。</div>';
    for(const p of board.parts||[]) {
      html+=`<details class="inline-card ai-layout-picker"><summary>${esc(p.display_code||p.code)} · ${esc(p.name)} · ${size(p.size_mm)}</summary><div class="inline-step-grid"><div>材料：${esc(p.material||'未取得')}</div><div>尺寸：${size(p.size_mm)}</div><div>每套用量：${esc(p.quantity??'未取得')}</div><div>尺寸来源：${esc(p.size_source)}</div>${p.gsm?`<div>克重：${esc(p.gsm)} g/㎡</div>`:''}${p.thickness_mm?`<div>厚度：${esc(p.thickness_mm)} mm</div>`:''}</div>${preview(board,p.code)}`;
      for(const s of p.sections||[])html+=`<div class="inline-row">${esc(s.name)} · ${esc(s.material||'材料未取得')} · ${size(s.size_mm)} · 每套用量 ${esc(s.quantity??'未取得')}</div>`;
      html+='</details>';
    }
    return html+'</section>';
  }
  function schemeHeader(board) {
    let html='<section class="inline-card"><div class="inline-card-title">当前方案</div><div class="inline-row">'+esc(board.selected_scheme_id||'当前图纸')+' '+preview(board,'','查看原图')+'</div>';
    if((board.schemes||[]).length>1)html+=(board.schemes||[]).map(s=>`<button class="inline-action" type="button" data-pkg-scheme="${esc(s.scheme_id||s.id)}" aria-pressed="${String((s.scheme_id||s.id)===board.selected_scheme_id)}">${esc(s.name||s.label||'方案 '+(s.scheme_id||s.id))}</button>`).join('');
    return html+'</section>';
  }
  function cuts(board) {
    const settings=board.cut_settings||{};
    let html='<section class="inline-card"><div class="inline-card-title">无排模零件 / 组成的开料</div><div class="inline-hint">沿用统一开料规则，材料专属余量优先；修改配置后第4阶段重算。</div>';
    html+=`<div class="ai-cut-allowance-bar"><label>默认长余量(mm) <input type="number" min="0" step="0.01" data-pkg-margin-length value="${esc(settings.cut_margin_length_mm??0)}"></label><label>默认宽余量(mm) <input type="number" min="0" step="0.01" data-pkg-margin-width value="${esc(settings.cut_margin_width_mm??5)}"></label><button class="inline-action save" type="button" data-pkg-save-allowance>保存开料余量</button></div>`;
    const materials=new Set((board.individual_cuts||[]).map(p=>p.material_code).filter(Boolean));
    for(const code of materials){const configured=settings.cut_allowances?.[code]||{};
      html+=`<div class="inline-edit-grid" data-pkg-material="${esc(code)}"><label>${esc(code)} 长余量(mm)</label><input type="number" min="0" step="0.01" data-pkg-material-length value="${esc(configured.length_mm??settings.cut_margin_length_mm??0)}"><label>宽余量(mm)</label><input type="number" min="0" step="0.01" data-pkg-material-width value="${esc(configured.width_mm??settings.cut_margin_width_mm??5)}"></div>`;}
    for(const p of board.individual_cuts||[]) html+=`<div class="inline-row"><b class="ai-layout-part-title">${esc(p.code)} · ${esc(p.name)}</b><div>展开尺寸：${size(p.size_mm)}</div><div>余量：${(p.allowance_mm||[]).map(n=>Number(n).toFixed(2)).join(' × ')} mm</div><div>最终开料尺寸：${size(p.sheet_mm)}</div><div>来源：${esc(p.size_source)}</div>${preview(board,p.code,'查看展开原图')}</div>`;
    if(!(board.individual_cuts||[]).length)html+='<div class="inline-hint">当前零件均已关联排模。</div>';
    return html+'</section>';
  }
  function stepHtml(step) {
    const labels={machine_length:'加工长(mm)',machine_width:'加工宽(mm)',imposition_count:'排版模数',
      labor_seconds:'工时(秒)',labor_rate:'人工费率',hot_area_mm2:'烫金面积(mm²)',formula_code:'计费公式'};
    const parameters=step.cost_parameters||{};
    const evidence=step.recommendation?.kind==='rule'?'<div class="inline-hint">'+esc(step.recommendation.reason||'基础组装估算')+'</div>'
      :window.PackagingProcessPresentation?.render(step.recommendation)||'<div class="inline-hint">推荐依据未提供，可查看原记录。</div>';
    const timeSources={poc_mock:'估算',unknown:'未提供来源',approved_standard:'库内工时',model_estimate:'AI估算'};
    const time=step.duration_min!=null?`${esc(step.duration_min)} 分 · ${esc(timeSources[step.time_source]||step.time_source||'来源未记录')}`:'未提供工时';
    return `<div class="inline-step"><div class="inline-sno">${esc(step.step_no)}</div><div class="inline-sbody"><div class="inline-step-title">${esc(step.name||step.step_name)}</div>${evidence}<div class="inline-step-grid"><div>设备：${esc(step.equipment||'未提供')}</div><div>适用要求：${esc(step.description||step.quality||step.note||'未提供')}</div><div>工时：${time}</div><div>先后依赖：${esc((step.depends_on||[]).join('、')||'无前序')}</div></div><details class="inline-row"><summary>计费参数</summary>${Object.entries(parameters).filter(([k])=>!['mock','source'].includes(k)).map(([k,v])=>`<div>${esc(labels[k]||k)}：${esc(typeof v==='object'?JSON.stringify(v):v)}</div>`).join('')||'<div>未提供参数</div>'}${step.formula_code?`<div>计费公式：${esc(step.formula_code)}</div>`:''}</details></div></div>`;
  }
  function processes(board) {
    let html=`<section class="inline-card"><div class="inline-card-title">3.2 组装工艺</div><div class="inline-hint">只列零件之间新增的工序，零件制造工艺在2.1查看。</div><div class="inline-totals"><span>新增工序 ${(board.assembly_steps||[]).length}</span><span>${board.status?.['3.2']?.completed?'已采用':'待采用'}</span></div>`;
    if(board.assembly_scope_changed)html+='<div class="inline-hint">旧工艺或当前方案已变化，请重新生成新增组装工艺；历史结果保留。</div>';
    if(board.process_missing)html+=`<div class="inline-hint">${esc(board.process_missing)} 个零件制造工艺待补齐，生成组装工艺时只补缺失零件。</div>`;
    if((board.assembly_questions||[]).length)html+='<details class="inline-row"><summary>连接依据待补 · '+board.assembly_questions.length+' 项</summary>'+board.assembly_questions.map(q=>'<div class="inline-hint">'+esc(q.field||'连接关系')+'：'+esc(q.reason||'')+'</div>').join('')+'</details>';
    html+='</section>';
    for(const [index,step] of (board.assembly_steps||[]).entries()){
      const codes=step.part_codes||[];
      const names=(board.parts||[]).filter(p=>codes.includes(p.code)).map(p=>p.name||p.code);
      html+=`<section class="inline-card" data-pkg-assembly-step="${index}">${stepHtml(step)}<div class="inline-row">关联零件：${names.map(esc).join('、')}</div><details class="ai-layout-picker"><summary>调整关联零件 · 已选 ${codes.length} 项</summary><div class="ai-layout-options">${(board.parts||[]).map(p=>`<label class="ai-layout-option"><input type="checkbox" data-pkg-assembly-part="${esc(p.code)}"${codes.includes(p.code)?' checked':''}><span>${esc(p.name||p.code)}</span></label>`).join('')}</div></details><button type="button" class="inline-action save" data-pkg-save-assembly="${index}">保存工序关联</button></section>`;
    }
    if(!(board.assembly_steps||[]).length)html+='<section class="inline-card"><div class="inline-empty">'+esc(board.assembly_summary||'尚无新增组装工艺，请生成组装工艺。')+'</div></section>';
    return html;
  }
  function render(tab,board) {
    if(!board)return '<div class="inline-empty">包装工作区正在读取…</div>';
    return ({drawings:cuts,params:cuts,process:processes,scheme:schemeHeader}[tab]||cuts)(board);
  }
  window.PackagingStageWorkspace={render,size};
})();
