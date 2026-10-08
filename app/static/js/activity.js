/* Server-confirmed counts, no automatic history read, no focus movement. */
(() => {
  document.querySelectorAll('[data-notification-open]').forEach(form => {
    let busy = false;
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (busy) return;
      busy = true;
      const button = form.querySelector('button');
      const error = form.querySelector('[data-notification-error]');
      button.disabled = true;
      error.textContent = '';
      try {
        const response = await fetch(form.action, {method: 'POST', body: new FormData(form), credentials: 'same-origin', headers: {Accept: 'application/json'}});
        if (!response.ok) throw new Error('Request failed');
        const result = await response.json();
        const count = result.unread_count;
        const target = new URL(result.destination, window.location.origin);
        if (!Number.isInteger(count) || count < 0 || target.origin !== window.location.origin) throw new Error('Invalid response');
        const row = form.closest('[data-notification-item]');
        row.classList.remove('notification-unread');
        row.querySelector('.notification-state').textContent = 'Read';
        const bell = document.querySelector('[data-notification-bell]');
        if (bell) {
          bell.setAttribute('aria-label', `Notifications, ${count} unread`);
          let badge = bell.querySelector('.notification-badge');
          if (count === 0) { if (badge) badge.remove(); }
          else {
            if (!badge) { badge = document.createElement('span'); badge.className = 'notification-badge'; bell.appendChild(badge); }
            badge.textContent = count > 99 ? '99+' : String(count);
          }
        }
        window.location.assign(target.href);
      } catch (_) {
        error.textContent = 'Could not open this notification. Please try again.';
        busy = false;
        button.disabled = false;
      }
    });
  });
  // Separate authenticated invocation after the stock transaction's redirect.
  // Bounded prompt delivery only; a configured scheduler recovers abandoned/retry jobs.
  const trigger = document.querySelector('[data-email-dispatch]');
  if (trigger) {
    (async () => {
      for (let i = 0; i < 10; i += 1) {
        try {
          const response = await fetch(trigger.action, {method: 'POST', body: new FormData(trigger), credentials: 'same-origin', headers: {Accept: 'application/json'}});
          if (!response.ok) break;
          const result = await response.json();
          if (!['sent', 'suppressed', 'failed', 'retry'].includes(result.result)) break;
        } catch (_) { break; }
      }
    })();
  }
})();
