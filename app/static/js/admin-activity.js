'use strict';
document.addEventListener('click', async event => {
  const link = event.target.closest('[data-load-older]');
  if (!link) return;
  event.preventDefault();
  if (link.getAttribute('aria-disabled') === 'true') return;
  const status = document.getElementById('activity-load-status');
  const timeline = document.getElementById('activity-days');
  link.setAttribute('aria-disabled', 'true');
  status.textContent = 'Loading older activity…';
  try {
    const response = await fetch(link.href, {credentials: 'same-origin', headers: {Accept: 'text/html'}});
    if (!response.ok) throw new Error('Older activity could not be loaded. Please try again.');
    const page = new DOMParser().parseFromString(await response.text(), 'text/html');
    const incoming = page.getElementById('activity-days');
    if (!incoming) throw new Error('Refresh the page and sign in again to continue.');
    const pager = page.getElementById('activity-pagination');
    if (!pager) throw new Error('Older activity could not be loaded. Please refresh the page.');
    const loaded = new Set([...timeline.querySelectorAll('[data-event]')].map(item => item.dataset.event));
    for (const group of [...incoming.children]) {
      for (const item of [...group.querySelectorAll('[data-event]')]) {
        if (loaded.has(item.dataset.event)) item.remove();
        else loaded.add(item.dataset.event);
      }
      if (!group.querySelector('[data-event]')) continue;
      const existing = [...timeline.children].find(item => item.dataset.day === group.dataset.day);
      if (existing) existing.querySelector('ol').append(...group.querySelector('ol').children);
      else timeline.append(group);
    }
    document.getElementById('activity-pagination').replaceWith(pager);
    status.textContent = 'Older activity loaded.';
  } catch (error) {
    status.textContent = error.message;
    link.removeAttribute('aria-disabled');
  }
});
