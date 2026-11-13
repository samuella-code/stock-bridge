(() => {
  const page = document.querySelector('[data-notifications]');
  if (!page) return;
  const filters = page.querySelector('[data-notification-filters]');
  const scope = page.querySelector('[data-notification-filter-scope]');
  const empty = page.querySelector('[data-notification-empty]');
  const defaultEmpty = page.querySelector('[data-notification-default-empty]');
  const count = page.querySelector('[data-notification-unread-count]');
  const cards = [...page.querySelectorAll('[data-notification-item]')];
  const pagination = page.querySelectorAll('.sb-notification-pages a');

  function showFilter() {
    const requested = window.location.hash.slice(1);
    const filter = ['unread', 'stock'].includes(requested) ? requested : 'all';
    filters.querySelector(`[value="${filter}"]`).checked = true;
    let matches = 0;
    for (const card of cards) {
      card.hidden = filter === 'unread' && !card.classList.contains('notification-unread')
        || filter === 'stock' && !card.hasAttribute('data-stock-alert');
      if (!card.hidden) matches += 1;
    }
    if (defaultEmpty) defaultEmpty.hidden = filter !== 'all';
    empty.hidden = filter === 'all' || matches > 0;
    empty.textContent = empty.hidden ? '' : filter === 'unread'
      ? 'There are no unread notifications on this page.'
      : 'There are no stock alerts on this page.';
    for (const link of pagination) link.hash = filter === 'all' ? '' : filter;
  }

  filters.addEventListener('change', event => {
    const url = new URL(window.location.href);
    url.hash = event.target.value === 'all' ? '' : event.target.value;
    window.history.replaceState(null, '', url);
    showFilter();
  });
  window.addEventListener('hashchange', showFilter);
  window.addEventListener('pageshow', showFilter);
  page.addEventListener('notification:opened', event => {
    if (count) count.textContent = String(event.detail);
  });
  filters.hidden = false;
  if (scope) scope.hidden = false;
  showFilter();
})();
