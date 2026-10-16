(function (global) {
  'use strict';
  function createUploader(options) {
    let busy = false;
    let sequence = 0;
    async function upload(file) {
      if (!file || busy || options.getProjectId() !== options.projectId) return;
      busy = true;
      const id = 'drawing-upload:' + options.projectId + ':' + Date.now() + ':' + (++sequence);
      const prompt = (options.hasSource ? '更换项目图纸：' : '上传项目图纸：') + file.name;
      const log = [];
      const report = (status, text) => { log.push(text); options.onProgress?.({projectId: options.projectId,
        taskId: id, runId: id, prompt, label: '保存项目图纸', status, log: log.slice()}); };
      try {
        const policy = global.CPQFilePolicy;
        if (policy) await policy.ready;
        const invalid = policy ? policy.validate(file, 'drawing')
          : (!/\.(dwg|dxf|pdf|png|jpe?g|webp|gif|bmp|step|stp|sldprt|stl|sat)$/i.test(file.name)
            ? '此文件格式不能作为图纸上传' : file.size > 16 * 1024 * 1024 ? '单文件不能超过16MiB' : '');
        if (invalid) throw new Error(invalid);
        if (options.hasSource && options.confirmReplace && !(await options.confirmReplace())) return;
        if (options.getProjectId() !== options.projectId) return;
        report('running', '正在保存图纸到当前项目…');
        const body = new FormData(); body.append('file', file);
        const response = await options.request('/api/projects/' + encodeURIComponent(options.projectId) + '/source',
          {method: 'POST', body});
        const result = await response.json();
        if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : result.detail?.message || '图纸保存失败');
        report('succeeded', '图纸已保存，可以开始解析；不会自动解析或重建项目。');
        if (options.getProjectId() === options.projectId) {
          try { await options.onSaved(options.projectId); }
          catch (_) { report('succeeded', '图纸已保存，但页面读取失败，请重新打开当前项目。'); }
        }
      } catch (error) {
        report('failed', error.message || '图纸保存失败，请重试');
      } finally { busy = false; }
    }
    return {upload, get busy() { return busy; }};
  }
  function update(root, options) {
    if (!root) return;
    root.hidden = !options.projectId;
    if (root.hidden) return;
    const hasSource = Boolean(options.filename);
    root.classList.toggle('has-source', hasSource);
    root.replaceChildren();
    const title = document.createElement('strong');
    title.textContent = hasSource ? '当前项目图纸：' + options.filename : '上传项目图纸';
    const hint = document.createElement('p');
    hint.textContent = hasSource ? '已有图纸，可直接开始解析。' : '点击选择或将图纸拖到此处，保存后点击「开始解析」。无需重新创建需求。';
    const input = document.createElement('input');
    input.type = 'file'; input.hidden = true; input.dataset.filePurpose = 'drawing';
    input.accept = global.CPQFilePolicy?.accept('drawing') || '.dwg,.dxf,.pdf,image/*,.step,.stp,.sldprt,.stl,.sat';
    input.setAttribute('aria-label', hasSource ? '选择替换图纸' : '选择项目图纸');
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'inline-action';
    button.textContent = hasSource ? '更换图纸' : '选择图纸文件';
    const state = document.createElement('div'); state.className = 'drawing-upload-state';
    state.setAttribute('role', 'status'); state.setAttribute('aria-live', 'polite');
    const controller = createUploader({...options, hasSource,
      confirmReplace: () => global.confirm('更换图纸后，已有解析、工艺和成本需重新核对与计算。是否继续？'),
      onProgress: event => { button.disabled = event.status === 'running'; state.textContent = event.log.at(-1);
        state.setAttribute('role', event.status === 'failed' ? 'alert' : 'status');options.onProgress?.(event); }
    });
    button.onclick = () => input.click();
    input.onchange = () => { const file = input.files?.[0]; input.value = ''; controller.upload(file); };
    root.ondragover = event => { event.preventDefault(); root.classList.add('drag-over'); };
    root.ondragleave = () => root.classList.remove('drag-over');
    root.ondrop = event => { event.preventDefault(); root.classList.remove('drag-over');controller.upload(event.dataTransfer?.files?.[0]); };
    root.append(title, hint, button, input, state);
  }
  global.DrawingUploadPanel = {createUploader, update};
})(window);
