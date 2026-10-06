// Browser-local preference only. Access and setup progress stay server-owned.
(() => {
  document.querySelectorAll('[data-welcome-key]').forEach(welcome => {
    const key = welcome.dataset.welcomeKey;
    try { if (window.localStorage.getItem(key) === 'dismissed') welcome.hidden = true; }
    catch (_) { /* Guidance stays usable when browser storage is unavailable. */ }
    function remember() {
      try { window.localStorage.setItem(key, 'dismissed'); } catch (_) { /* No schema fallback. */ }
    }
    welcome.querySelector('[data-welcome-complete]')?.addEventListener('click', remember);
    const skip = welcome.querySelector('[data-welcome-dismiss]');
    if (skip) {
      skip.hidden = false;
      skip.addEventListener('click', () => {
        remember();
        welcome.hidden = true;
        document.querySelector('#business-setup summary')?.focus();
      });
    }
  });
})();
