(() => {
  const form = document.querySelector('.reminder-form');
  if (!form) return;
  const message = form.querySelector('textarea');
  const override = form.querySelector('#reminder-override');
  function update() {
    form.querySelectorAll('[data-capped]').forEach(input => {
      input.disabled = !override.checked;
      if (input.disabled) input.checked = false;
    });
    form.querySelector('#override-reason').required = override.checked;
    const count = form.querySelectorAll('input[name=owners]:checked:not(:disabled)').length;
    form.querySelector('[data-send-reminders]').textContent = `Send to ${count} ${count === 1 ? 'owner' : 'owners'}`;
    form.querySelector('[data-recipient-count]').textContent = `${count} selected. Eligibility is checked again before sending.`;
  }
  form.addEventListener('change', update);
  form.querySelectorAll('[data-placeholder]').forEach(button => button.addEventListener('click', () => {
    message.setRangeText(`{${button.dataset.placeholder}}`, message.selectionStart, message.selectionEnd, 'end');
    message.focus();
  }));
  update();
})();
