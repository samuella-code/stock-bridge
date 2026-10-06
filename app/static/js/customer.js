// Presentation only: every mutation still uses its existing protected form.
(() => {
  document.querySelectorAll('[data-price-interval]').forEach(button => {
    button.addEventListener('click', () => {
      const interval = button.dataset.priceInterval;
      document.querySelectorAll('[data-price-interval]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
      document.querySelectorAll('[data-price-monthly]').forEach(item => { item.textContent = item.dataset[interval === 'yearly' ? 'priceYearly' : 'priceMonthly']; });
      document.querySelectorAll('.billing-plan-grid select[name="interval"]').forEach(select => { select.value = interval; });
    });
  });
  const profileButton = document.getElementById('profileButton');
  const profile = document.getElementById('profileDropdown');
  function closeMenus() {
    document.querySelectorAll('.business-switcher[open]').forEach(menu => menu.removeAttribute('open'));
    profile?.classList.remove('open');
    profileButton?.setAttribute('aria-expanded', 'false');
  }
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    const business = document.querySelector('.business-switcher[open]');
    const profileOpen = profile?.classList.contains('open');
    closeMenus();
    if (business) business.querySelector('summary')?.focus();
    else if (profileOpen) profileButton?.focus();
  });
  document.addEventListener('click', event => {
    if (!event.target.closest('.business-switcher')) document.querySelectorAll('.business-switcher[open]').forEach(menu => menu.removeAttribute('open'));
    if (!event.target.closest('.profile-menu')) profileButton?.setAttribute('aria-expanded', 'false');
  });
  document.querySelectorAll('form[method="POST"],form[method="post"]').forEach(form => {
    form.addEventListener('submit', event => {
      if (form.dataset.submitting === 'true') { event.preventDefault(); return; }
      const consequential = form.querySelector('.danger-link') || form.action.endsWith('/cancel') || form.action.includes('/adjust');
      if (consequential && !window.confirm(form.action.endsWith('/cancel') ? 'Stop renewal? Your already-paid access remains until the end of its period.' : 'Confirm this change. The reason and change will remain in your history.')) { event.preventDefault(); return; }
      queueMicrotask(() => {
        if (event.defaultPrevented) return;
        form.dataset.submitting = 'true';
        form.setAttribute('aria-busy', 'true');
        // Keep named submitters enabled so after_save and other values reach Flask.
        form.querySelectorAll('button[type="submit"],button:not([type])').forEach(button => { button.setAttribute('aria-disabled', 'true'); });
      });
    });
  });
  window.addEventListener('pageshow', () => {
    document.querySelectorAll('form[data-submitting]').forEach(form => {
      delete form.dataset.submitting;
      form.removeAttribute('aria-busy');
      form.querySelectorAll('[aria-disabled]').forEach(button => button.removeAttribute('aria-disabled'));
    });
  });
})();
