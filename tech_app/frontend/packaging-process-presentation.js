/* 包装工序展示：来源与参考相似度，不参与审批和成本计算。 */
(() => {
  'use strict';
  const esc = value => String(value ?? '').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
  function processRecommendation(step, context = {}) {
    if (step.recommendation) return step.recommendation;
    if (!context.packaging) return null;
    const sections = context.sections || [];
    const section = sections.find(s => s.section_id === step.section_id);
    const coverage = section?.coverage || context.coverage || {};
    const source = String(coverage.process_origin || '');
    const lookup = coverage.route_lookup || {};
    const reference = source === 'da_packaging_reference' || source === 'approved_da_packaging_route';
    return {kind: reference ? 'reference' : 'ai',
      reason: reference ? '来自数据库参考件路线' : '根据当前零件要求推荐；历史记录未提供逐工序推荐理由',
      references: coverage.reference_routes?.length ? coverage.reference_routes : (lookup.routes || [])};
  }

  function referenceSimilarity(ref) {
    const value = ref.similarity_score;
    return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1
      ? ` ${Math.round(value * 100)}%` : '：未评分';
  }

  function processRecommendationHtml(item) {
    if (!item) return '';
    const refs = item.references || [];
    const label = item.kind === 'reference'
      ? '参考件相似度' + (refs.length ? referenceSimilarity(refs[0]) : '：未评分') : 'AI 推荐';
    const labels = {name:'名称/用途', material:'材料', size:'尺寸', verified_size:'已核对尺寸',
      size_is_estimated_not_verified:'尺寸为估算', exact_name:'名称命中', normalized_name:'归一化名称命中',
      product_item_code:'零件编码命中', feature_similarity:'材料/尺寸/名称相似匹配'};
    const scoreDetails = ref => Object.entries(ref.scores || {}).map(([key, value]) =>
      `${labels[key] || key}：${referenceSimilarity({similarity_score:value}).replace(/^：/, '')}`).join('；') || '未评分';
    return `<details class="inline-row"><summary>${esc(label)} · 查看依据</summary>`
      + `<div class="inline-hint">${esc(item.reason || '未提供推荐理由')}；相似度描述参考零件匹配程度，不代表工序正确率。</div>`
      + refs.map(ref => `<div class="inline-row"><b>${esc(ref.reference_name || ref.header?.name || '库内参考件')}</b> ${esc(ref.reference_code || ref.header?.product_item_code || '')}`
        + `<div>参考件相似度${esc(referenceSimilarity(ref))} · 匹配方式：${esc(labels[ref.match_method] || ref.match_method || '未记录')}</div>`
        + `<div>评分依据：${esc(scoreDetails(ref))}；缺失特征：${esc((ref.missing_features || []).map(key => labels[key] || key).join('、') || '未记录')}</div>`
        + `<div>${esc((ref.steps || []).map(s => s.operation_name || s.name || '').join(' → '))}</div></div>`).join('')
      + '</details>';
  }

  window.PackagingProcessPresentation = {resolve: processRecommendation, render: processRecommendationHtml};
})();
