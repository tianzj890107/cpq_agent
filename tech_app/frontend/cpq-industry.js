/* 【CPQ 定制 · 本文件不来自 process_drawing，同步上游时保留】
   需求单的行业模板（半导体/电池/电器）决定「三、产品技术规格」整段用哪套字段，
   所以必须在建单前选。上游是在 tech_app 自己的 home.html 里选（home.js 的
   #homeIndustry）；而嵌进 CPQ 后入口换成了「报价首页.html 的技术工艺标签」，
   那里选完通过 URL ?industry= 带过来。

   两条路径：
     · 首页传了图纸 -> 项目和需求草稿在首页就建好了，data.industry 已写入，
       本文件不介入（URL 上也不带 industry）。
     · 首页没传图纸 -> 直接跳到本页，此时还没有需求单。上游此时把行业锁死为默认的
       「半导体」（rcData() 是空的），用户在首页选的「电池」会被静默丢掉。
       本文件补上这一段：先用 URL 带来的值渲染，并在第一次保存时写进需求单。 */
(function () {
  const OK = ['semiconductor', 'battery', 'appliance'];
  const fromUrl = String(new URLSearchParams(location.search).get('industry') || '').toLowerCase();
  if (!OK.includes(fromUrl)) return;          // 没带参数就完全不介入，保持上游行为
  let pending = fromUrl;                      // 还没落库的行业选择

  // 需求单还没建时 rcData() 是空的 —— 让行业回退到首页选的那个
  const baseIndustry = rcIndustry;
  rcIndustry = function () {
    const saved = String((rcData() || {}).industry || '').toLowerCase();
    if (OK.includes(saved) || saved === 'flexible') return baseIndustry();
    return pending;
  };

  // 上游在「还没有需求单」时禁止切换（提示回首页选）。这里已经有首页带来的选择，
  // 允许继续在本页改，改的是 pending，随第一次保存落库。
  const baseBind = rcBindIndustrySpec;
  rcBindIndustrySpec = function () {
    if (rcRequirement) return baseBind();
    const sel = document.querySelector('#rcIndustry');
    if (!sel) return;
    sel.value = rcIndustry();
    sel.onchange = () => { pending = sel.value; renderRequirementCreate(); };
  };

  // 第一次保存时才真正建项目/需求单，行业要一起写进去，否则后端按默认的半导体存。
  // 这里只补一个空壳；上游那层 rcPersist 包装（industry / flexible_spec）会接着填。
  const basePersist = rcPersist;
  rcPersist = async function (submit) {
    if (!rcRequirement) {
      const sel = document.querySelector('#rcIndustry');
      if (sel && OK.includes(sel.value)) pending = sel.value;
      rcRequirement = { requirement_no: '', data: { industry: pending, industry_selection: pending } };
    }
    return basePersist(submit);
  };
})();
