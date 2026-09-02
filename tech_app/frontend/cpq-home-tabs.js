/* 【CPQ 定制 · 本文件不来自 process_drawing，同步上游时保留】
   技术工艺的首页 = 本页（tech_app 的 home.html），由 8010 反向代理提供。
   点「技术工艺」从报价首页跳到这里之后，顶部要和 8010 主页保持一致：
   同一句问候语 + 同一排四个助手标签，「技术工艺」高亮，点其余三个跳回报价首页。

   只改顶部这两块。下面的「新建需求 / 我的清单 / 全部清单」是 tech_app 自己的
   工作台（含行业模板选择 #homeIndustry），照原样保留——那才是这个页面的正事。

   实现方式：包住 renderHome() 而不是改 home.js。上游 home.js:369 会在
   renderHome 里给 .category-tag 绑定自己的 onclick（弹「正在接入」提示），
   所以必须等它跑完再覆盖，否则点标签只会看到那个 toast。 */
(function () {
  const CPQ_HOME = '/' + encodeURIComponent('报价首页.html');
  const TABS = [
    { label: '报价助手', icon: 'ti-brain', mode: 'quote' },
    { label: '配置助手', icon: 'ti-settings', mode: 'config' },
    { label: '规则助手', icon: 'ti-scale', mode: 'rule' },
    { label: '技术工艺', icon: 'ti-tools', mode: 'tech', active: true },
  ];

  function applyCpqHeader() {
    const title = document.querySelector('.greeting-title');
    if (title) title.innerHTML = '<span class="highlight">创作无限可能</span>，智能报价';
    const subtitle = document.querySelector('.greeting-subtitle');
    if (subtitle) subtitle.textContent = '今天我能帮你做些什么？';

    const bar = document.querySelector('.category-tags');
    if (!bar) return;
    bar.textContent = '';
    for (const tab of TABS) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'category-tag' + (tab.active ? ' active' : '');
      // 图标来自 8010 的 vendor/tabler-icons（同源，见 home.html 里那行 link）。
      // 单独跑 8012 时这个 CSS 取不到，图标位会是空的，文字标签仍然可读。
      const icon = document.createElement('i');
      icon.className = 'ti ' + tab.icon;
      btn.append(icon, document.createTextNode(' ' + tab.label));
      if (!tab.active) {
        btn.addEventListener('click', () => {
          window.location.href = CPQ_HOME + '?assistant=' + tab.mode;
        });
      }
      bar.appendChild(btn);
    }
  }

  applyCpqHeader();          // home.js 末尾的 startHome() 已经同步渲染过一轮
  if (typeof renderHome === 'function') {   // 之后若再重渲染，跟着补一次
    const baseRenderHome = renderHome;
    renderHome = function () { baseRenderHome(); applyCpqHeader(); };
  }
})();
