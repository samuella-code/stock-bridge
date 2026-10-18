// Customer UI only. Keep the original input and never submit or store passwords.
(() => {
  document.querySelectorAll('[data-customer-password-toggle]').forEach(button => {
    const input = document.getElementById(button.dataset.customerPasswordToggle);
    if (!input || input.type !== 'password') return;
    const label = document.querySelector(`label[for="${input.id}"]`);
    const name = label ? label.textContent.trim().toLowerCase() : 'password';
    button.hidden = false;
    button.addEventListener('click', () => {
      const visible = input.type === 'password';
      input.type = visible ? 'text' : 'password';
      button.setAttribute('aria-label', `${visible ? 'Hide' : 'Show'} ${name}`);
      button.setAttribute('aria-pressed', String(visible));
      button.querySelector('[data-eye-open]').hidden = visible;
      button.querySelector('[data-eye-off]').hidden = !visible;
    });
  });
})();
