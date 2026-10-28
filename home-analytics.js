/* The remote page owns analysis execution; this module only embeds and passes text. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.HomeAnalytics = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const ANALYTICS_URL = 'http://pdt-dev.eimos.com/api/ontology-agentic-analytics';
  function buildUrl(prompt) {
    const url = new URL(ANALYTICS_URL);
    url.searchParams.set('prompt', prompt);
    url.searchParams.set('theme', 'light');
    url.searchParams.set('layout', 'single');
    return url.href;
  }
  function createController(refs, clock) {
    let mode = 'quote', opened = false, editing = false, prompt = '', timer;
    const timers = clock || globalThis;
    function render() {
      const active = mode === 'analytics';
      const viewing = active && opened && !editing;
      refs.panel.hidden = !viewing;
      refs.list.hidden = true;
      refs.greeting.hidden = viewing;
      refs.composer.hidden = viewing;
      refs.nav.hidden = viewing;
      refs.tabs.hidden = viewing;
      refs.resume.hidden = !(active && opened && editing);
      refs.body.classList.toggle('analytics-fullscreen', viewing);
      refs.page.classList.toggle('analytics-open', viewing);
      refs.send.textContent = '';
      refs.send.innerHTML = '<i class="ti ti-arrow-up" aria-hidden="true"></i>';
      refs.send.setAttribute?.('aria-label', '发送需求');
    }
    function load() {
      timers.clearTimeout(timer);
      refs.status.hidden = false;
      refs.status.textContent = '正在加载经营分析页面…';
      refs.frame.setAttribute?.('aria-busy', 'true');
      refs.external.href = buildUrl(prompt);
      // Assign only on explicit submit/retry. Tab switching preserves the embedded session.
      refs.frame.src = refs.external.href;
      timer = timers.setTimeout(function () {
        refs.status.hidden = false;
        refs.status.textContent = '加载时间较久，可重试或独立打开。网络、登录及嵌入权限由分析服务提供。';
        refs.frame.setAttribute?.('aria-busy', 'false');
      }, 15000);
    }
    refs.frame.addEventListener('load', function () {
      if (!opened) return;
      timers.clearTimeout(timer);
      refs.frame.setAttribute?.('aria-busy', 'false');
      refs.status.hidden = true;
      refs.status.textContent = '';
    });
    refs.frame.addEventListener('error', function () {
      timers.clearTimeout(timer);
      refs.status.hidden = false;
      refs.status.textContent = '分析页面加载失败，请重试或独立打开。';
      refs.frame.setAttribute?.('aria-busy', 'false');
    });
    refs.retry.addEventListener('click', function () { if (opened) load(); });
    refs.edit.addEventListener('click', function () {
      editing = true;
      refs.input.value = prompt;
      refs.input.dispatchEvent?.(new Event('input', { bubbles: true }));
      render();
      refs.input.focus();
    });
    refs.resume.addEventListener('click', function () {
      if (!opened) return;
      editing = false;
      render();
      refs.edit.focus?.({ preventScroll: true });
    });
    refs.copy.addEventListener('click', async function () {
      try {
        await globalThis.navigator.clipboard.writeText(prompt);
        refs.status.hidden = false;
        refs.status.textContent = '需求已复制，可在分析页面中粘贴。';
      } catch (_) {
        refs.status.hidden = false;
        refs.status.textContent = '未能自动复制，请展开“本次分析需求”手动复制。';
        refs.prompt.closest?.('details')?.setAttribute('open', '');
      }
    });
    return {
      setMode(next) { mode = next; render(); },
      open(text) {
        if (!String(text || '').trim()) { refs.input.focus(); return false; }
        prompt = String(text);
        refs.prompt.textContent = prompt;
        opened = true;
        editing = false;
        mode = 'analytics';
        render();
        load();
        refs.edit.focus?.({ preventScroll: true });
        return true;
      },
    };
  }
  let controller;
  function init(doc) {
    controller = createController({
      panel: doc.getElementById('analyticsPanel'), frame: doc.getElementById('analyticsFrame'),
      status: doc.getElementById('analyticsStatus'), external: doc.getElementById('analyticsExternal'),
      prompt: doc.getElementById('analyticsPrompt'), edit: doc.getElementById('analyticsEdit'),
      retry: doc.getElementById('analyticsRetry'), copy: doc.getElementById('analyticsCopy'),
      page: doc.querySelector('.page-container'), greeting: doc.querySelector('.greeting'),
      composer: doc.querySelector('.unified-input-card'), list: doc.querySelector('.list-section'),
      send: doc.querySelector('.send-btn'), input: doc.querySelector('.unified-textarea'),
      nav: doc.querySelector('.nav-container'), tabs: doc.querySelector('.category-tags'),
      resume: doc.getElementById('analyticsResume'), body: doc.body,
    });
  }
  return { buildUrl, createController, init,
    setMode(mode) { controller?.setMode(mode); },
    open(text) { return controller?.open(text); },
  };
});
