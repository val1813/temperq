/* Keep the upload state legible and keyboard accessible; no network until submit. */
(() => {
  const form = document.querySelector('.single-upload');
  if (!form) return;
  const inputs = [...form.querySelectorAll('input[type=file]')];
  function refresh() {
    let count = 0, bytes = 0, periods = 0;
    for (const input of inputs) {
      const files = [...input.files];
      count += files.length;
      bytes += files.reduce((sum, file) => sum + file.size, 0);
      if (input.dataset.slot.startsWith('backtest') && files.length) periods++;
      const slot = input.closest('.slot');
      slot.classList.toggle('slot-filled', files.length > 0);
      const name = slot.querySelector('.slot-name');
      name.textContent = files.length ? files.map(file => file.name).join('、') : '未选择文件';
      slot.querySelector('.slot-clear').hidden = files.length === 0;
    }
    const supplement = form.querySelector('select[name=supplement_period]');
    const supplementSlot = supplement?.value || '1';
    const periodSlots = new Set(inputs.filter(input => input.files.length && input.dataset.slot.startsWith('backtest')).map(input => input.dataset.slot.slice(-1)));
    if (inputs.some(input => input.files.length && !input.dataset.slot.startsWith('backtest') && input.dataset.slot !== 'code')) periodSlots.add(supplementSlot);
    periods = periodSlots.size;
    const button = form.querySelector('button[type=submit]');
    if (!button.disabled) button.textContent = periods ? `提交全部 ${periods} 个时期审计 →` : '提交审计 →';
    document.querySelector('#upload-progress').textContent = count
      ? `已选择 ${count} 个文件 · ${(bytes / 1000000).toFixed(1)} MB${periods ? ` · 全部 ${periods} 个时期将一起审计` : ''}`
      : '尚未选择文件';
  }
  inputs.forEach(input => input.addEventListener('change', refresh));
  form.querySelector('select[name=supplement_period]')?.addEventListener('change', refresh);
  form.querySelectorAll('[data-clear]').forEach(button => button.addEventListener('click', () => {
    document.querySelector('#upload-' + button.dataset.clear).value = '';
    refresh();
  }));
  form.addEventListener('submit', event => {
    const files = inputs.flatMap(input => [...input.files]);
    if (!files.length) { event.preventDefault(); document.querySelector('#upload-progress').textContent = '请至少选择策略代码或回测数据'; return; }
    if (files.some(file => file.size > 8000000) || files.reduce((n, f) => n + f.size, 0) > 24000000) {
      event.preventDefault(); document.querySelector('#upload-progress').textContent = '文件超过限制：单文件8MB，合计24MB'; return;
    }
    const button = form.querySelector('button[type=submit]');
    button.disabled = true;
    button.textContent = '正在逐期审计…';
    document.querySelector('#upload-progress').textContent = '请等待报告生成；材料较多时可能需要更长时间。';
  });
  window.addEventListener('pageshow', () => {
    const button = form.querySelector('button[type=submit]');
    button.disabled = false; button.textContent = '提交审计 →'; refresh();
  });
})();
