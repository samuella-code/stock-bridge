// Public presentation only. Prices and checkout validation remain server owned.
(() => {
  document.querySelectorAll('[data-price-interval]').forEach(button => {
    button.addEventListener('click', () => {
      const yearly = button.dataset.priceInterval === 'yearly';
      document.querySelectorAll('[data-price-interval]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
      document.querySelectorAll('[data-price-monthly]').forEach(item => { item.textContent = item.dataset[yearly ? 'priceYearly' : 'priceMonthly']; });
    });
  });
  const menu = document.querySelector('.entry-mobile-nav');
  menu?.querySelectorAll('a').forEach(link => link.addEventListener('click', () => { menu.open = false; }));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && menu?.open) { menu.open = false; menu.querySelector('summary')?.focus(); }
  });
})();
