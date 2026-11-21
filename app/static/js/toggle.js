(() => {
  const sync = input => input.setAttribute('aria-checked', String(input.checked));
  document.querySelectorAll('input.sb-toggle[role="switch"]').forEach(sync);
  document.addEventListener('change', event => {
    if (event.target.matches('input.sb-toggle[role="switch"]')) sync(event.target);
  });
  document.addEventListener('reset', event => {
    requestAnimationFrame(() => event.target.querySelectorAll('input.sb-toggle').forEach(sync));
  });
})();
