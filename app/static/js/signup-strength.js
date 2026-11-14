(() => {
  const password = document.getElementById('signup-password');
  const meter = document.querySelector('[data-signup-strength]');
  if (!password || !meter) return;

  const status = meter.querySelector('[role="status"]');
  const labels = ['Password strength', 'Password strength: weak', 'Password strength: fair', 'Password strength: strong'];

  function update() {
    const value = password.value;
    let level = 0;
    if (value) level = 1;
    if (value.length >= password.minLength) level = 2;
    if (value.length >= 12 && /[0-9\p{P}\p{S}]/u.test(value)) level = 3;
    meter.dataset.level = String(level);
    if (status.textContent !== labels[level]) status.textContent = labels[level];
  }

  password.addEventListener('input', update);
  update();
  meter.hidden = false;
})();
