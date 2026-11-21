(() => {
  const timings = document.querySelector('[data-timing-form]');
  if (timings) {
    const switches = [...timings.querySelectorAll('input[role="switch"]')];
    const initial = switches.map(input => input.checked);
    timings.addEventListener('change', () => {
      timings.querySelector('[data-save-timings]').disabled = !switches.some((input, i) => input.checked !== initial[i]);
    });
  }
  const form = document.querySelector('.reminder-form');
  if (!form) return;
  const message = form.querySelector('#reminder-message');
  const override = form.querySelector('#reminder-override');
  const owners = [...form.querySelectorAll('input[name="owners"]')];
  const preview = form.querySelector('[data-preview-message]');
  const previewBox = form.querySelector('#message-preview');
  const selected = () => owners.filter(input => input.checked && !input.disabled);
  const firstRow = () => selected()[0]?.closest('[data-recipient-row]');
  let previewSequence = 0;
  let previewController;
  let previewTimer;
  function cancelPreview() {
    clearTimeout(previewTimer);
    ++previewSequence;
    previewController?.abort();
  }
  async function renderPreview() {
    cancelPreview();
    const input = selected()[0];
    if (!input || previewBox.hidden) return;
    const sequence = previewSequence;
    const body = form.querySelector('[data-preview-body]');
    const notice = form.querySelector('[data-preview-notice]');
    body.textContent = 'Loading preview…';
    notice.textContent = '';
    previewController = new AbortController();
    const values = new FormData(form);
    values.set('owner', input.value);
    values.delete('action');
    values.delete('owners');
    values.delete('request_key');
    try {
      const response = await fetch(form.dataset.previewUrl, {
        method: 'POST', body: values, credentials: 'same-origin',
        headers: {Accept: 'application/json'}, signal: previewController.signal
      });
      if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('Refresh the page and sign in again to preview.');
      const result = await response.json();
      if (sequence !== previewSequence) return;
      if (!response.ok) throw new Error(result.error || 'The preview could not be loaded.');
      body.textContent = result.body;
      notice.textContent = result.notice;
    } catch (error) {
      if (sequence === previewSequence && error.name !== 'AbortError') body.textContent = error.message;
    }
  }
  function queuePreview() {
    cancelPreview();
    if (previewBox.hidden) return;
    form.querySelector('[data-preview-body]').textContent = 'Updating preview…';
    previewTimer = setTimeout(renderPreview, 350);
  }
  function update() {
    owners.filter(input => input.hasAttribute('data-capped')).forEach(input => {
      input.disabled = !override.checked;
      if (input.disabled) input.checked = false;
    });
    owners.forEach(input => input.closest('[data-recipient-row]').classList.toggle('is-disabled', input.disabled));
    form.querySelector('#override-reason').required = override.checked;
    const count = selected().length;
    const eligible = owners.filter(input => !input.disabled).length;
    const send = form.querySelector('[data-send-reminders]');
    send.textContent = `Send to ${count} ${count === 1 ? 'owner' : 'owners'}`;
    send.disabled = count === 0 || form.dataset.emailReady !== 'true';
    form.querySelector('[data-recipient-count]').textContent = `${count} selected of ${eligible} eligible`;
    form.querySelector('[data-selection-help]').hidden = count > 0;
    form.querySelector('[data-select-eligible]').disabled = eligible === 0;
    preview.textContent = firstRow() ? `Preview as ${firstRow().dataset.name}` : 'Preview as first recipient';
    preview.hidden = !firstRow();
    if (!firstRow()) {
      cancelPreview(); previewBox.hidden = true; preview.setAttribute('aria-expanded', 'false');
    } else if (!previewBox.hidden) queuePreview();
  }
  form.addEventListener('change', update);
  form.addEventListener('click', event => {
    const row = event.target.closest('[data-recipient-row]');
    if (row && !event.target.closest('input,a,button,label')) {
      const input = row.querySelector('input[name="owners"]');
      if (!input.disabled) { input.checked = !input.checked; update(); }
    }
    const include = event.target.closest('[data-open-override]');
    if (include) {
      event.preventDefault();
      form.querySelector('#frequency-override').open = true;
      override.focus();
    }
    const why = event.target.closest('[data-audience-why]');
    if (why) {
      const tip = document.getElementById(why.getAttribute('aria-controls'));
      tip.hidden = !tip.hidden;
      why.setAttribute('aria-expanded', String(!tip.hidden));
    }
  });
  form.querySelector('[data-select-eligible]').addEventListener('click', () => {
    owners.filter(input => !input.disabled).forEach(input => { input.checked = true; }); update();
  });
  preview.addEventListener('click', event => {
    event.preventDefault(); previewBox.hidden = !previewBox.hidden;
    preview.setAttribute('aria-expanded', String(!previewBox.hidden));
    if (previewBox.hidden) cancelPreview(); else renderPreview();
  });
  message.addEventListener('input', queuePreview);
  form.querySelector('#override-reason').addEventListener('input', queuePreview);
  update();
})();
