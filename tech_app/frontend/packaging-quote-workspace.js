/* 共用报价表的包装字段适配。只投影已有事实，绝不计算售价或发请求。 */
(function(global){
  'use strict';
  const addonDefinitions=[['freight','运输配送'],['financing','资金占用'],['insurance','保险'],
    ['commission','销售佣金'],['business_service','商务服务'],['rush','加急交付'],
    ['special_testing','专项检测认证'],['tech_premium','技术溢价'],['market_adjustment','市场调节'],['other_addon','其他加价']];
  const present=v=>v!==undefined && v!==null && v!=='';
  const number=v=>present(v) && Number.isFinite(Number(v))?Number(v):null;
  function quoteMatches(pkg,q){
    if(!pkg || !q)return false;
    const version=(pkg.source||{}).result_version || pkg.result_version || '';
    if(version && q.source_result_version!==version)return false;
    const cost=number((pkg.cost||{}).total_cost), quoted=number(q.source_total_cost ?? q.cost_total);
    return cost!==null && quoted!==null && Math.abs(cost-quoted)<1e-9;
  }
  function project(pkg,q,previousQuote){
    const req=pkg.requirement||{},cost=pkg.cost||{},box=pkg.box_type||{},source=pkg.source||{};
    const params={...req,...Object.fromEntries(Object.entries(pkg.params||{}).filter(([,v])=>present(v)))};
    const quantity=cost.quote_quantity ?? req.quote_quantity;
    const gaps=cost.gaps||pkg.gaps||[];
    const other=cost.others ?? (['labor_total','packaging_total'].every(k=>present(cost[k]))
      ? Number(cost.labor_total)+Number(cost.packaging_total)+Number(cost.other_total||0):null);
    const cash=v=>number(v)===null?null:Number(new Intl.NumberFormat('en-US',{useGrouping:false,minimumFractionDigits:2,maximumFractionDigits:2}).format(Number(v)));
    const row={'产品系列':'包装','产品型号':box.confirmed_box_type||req.box_type||'',
      '成品编码':box.confirmed_box_type||req.box_type||'','成品描述':req.packaging_product_name||req.title||'',
      '数量':quantity,'币种':cost.currency||req.currency||'', '基础成本':cash(cost.total_cost),
      '材料费用':cash(cost.material_total),'加工费用':cash(cost.process_total),'其他费用':cash(other),
      '整单成本':number(cost.total_cost)!==null && number(quantity)!==null?cash(cash(cost.total_cost)*Number(quantity)):null,
      '成本状态':cost.has_gaps||cost.stale||gaps.length||(pkg.gaps||[]).length||q?.draft?'暂估':'正式',
      '定价状态':q?'已定价':previousQuote?'待重算':'待定价',
      '成本结果版本':source.result_version||pkg.result_version||cost.estimate_id||''};
    const commercial=q||previousQuote||{};
    const margin={'定价方式':commercial.pricing_mode==='markup'?'成本加成率（历史口径）':'毛利率（成本 ÷ (1-毛利率)）',
      '毛利率（%）':commercial.pricing_mode==='gross_margin' && present(commercial.gross_margin_rate)?Number((commercial.gross_margin_rate*100).toFixed(8)):'',
      '基础成本':cost.total_cost,'利润加成':q?q.margin_price-q.cost_total:'','定价后单价':q?q.margin_price:''};
    const values=Object.fromEntries((commercial.addons||[]).map(x=>[x.code,x.amount]));
    const addons=addonDefinitions.map(([code,label])=>({'加价编码':code,'加价项目':label,'金额（元/只）':values[code] ?? ''}));
    if(q)Object.assign(row,{'价格':q.subtotal_unit ?? q.untaxed_unit_price,
      '利润加成':q.margin_price-q.cost_total,'其他加价':q.addon_total,
      '报价':q.subtotal_unit ?? q.untaxed_unit_price,'折扣':1-(q.discount_rate??0),
      '折后价格':q.net_unit_price??q.untaxed_unit_price,'总金额':q.untaxed_total,
      '税率':q.tax_rate,'税金':q.taxed_total-q.untaxed_total,'数量':q.quote_quantity});
    const stages=commercial.rule_pricing?.stages;
    for(const key of ['基础成本','材料费用','加工费用','其他费用','整单成本','价格','利润加成','其他加价','报价','折后价格','总金额','税金'])
      if(number(row[key])!==null)row[key]=Number(row[key]).toFixed(2);
    return {s2_products:[row],s2_techparams:[{...params,box_type_code:box.confirmed_box_type||req.box_type||'',name:req.packaging_product_name||req.title||''}],
      s3_markup:stages?.['3']?.markup||[],s4_markup:stages?.['4']?.markup||[],s5_detail:q?[{...row}]:[]};
  }
  function readInputs(marginRows,addonRows,detail,previous,pkg){
    const raw=(marginRows[0]||{})['毛利率（%）'];
    const margin=number(String(raw??'').replace(/%$/,''));
    if(margin===null || margin<0 || margin>=100)throw Error('请在定价规则填写0（含）到100（不含）的毛利率');
    const inputs={gross_margin_rate:margin/100,addons:{}};
    for(const row of addonRows){
      const value=row['金额（元/只）'];if(!present(value))continue;
      const amount=number(value);if(amount===null)throw Error('其他加价金额必须是有效数字');
      const code=row['加价编码'];if(!addonDefinitions.some(x=>x[0]===code))throw Error('未知加价项目');
      inputs.addons[code]=amount;
    }
    const q=previous||{},r=detail||{};
    const qty=r['数量'] ?? q.quote_quantity ?? (pkg.cost||{}).quote_quantity ?? (pkg.requirement||{}).quote_quantity;
    if(present(qty)){if(number(qty)===null || Number(qty)<=0)throw Error('数量必须为正数');inputs.quote_quantity=Number(qty);}
    const tax=r['税率'] ?? q.tax_rate;
    if(present(tax)){if(number(tax)===null || Number(tax)<0 || Number(tax)>1)throw Error('税率使用0到1的小数');inputs.tax_rate=Number(tax);}
    const factor=r['折扣'];
    if(present(factor)){
      if(number(factor)===null || Number(factor)<0 || Number(factor)>1)throw Error('折扣使用0到1的小数');
      inputs.discount={code:'discount',rate:1-Number(factor)};
    }else if(q.discount)inputs.discount=q.discount;
    return inputs;
  }
  global.PackagingQuoteWorkspace={project,readInputs,quoteMatches,addonDefinitions};
})(typeof window!=='undefined'?window:globalThis);
