// Reports only: keep preset selection and explicit dates consistent.
(() => {
  document.querySelectorAll('[data-report-period]').forEach(form => {
    const period = form.elements.namedItem('period');
    const dates = ['from', 'to'].map(name => form.elements.namedItem(name));
    if (!period || dates.some(input => !input)) return;
    dates.forEach(input => {
      ['input', 'change'].forEach(event => input.addEventListener(event, () => {
        period.value = 'custom';
      }));
    });
    period.addEventListener('change', () => {
      if (['today', 'week', 'month'].includes(period.value)) {
        dates.forEach(input => { input.value = ''; });
      }
    });
    // Includes browser-restored values. Server validation remains authoritative.
    if (dates.some(input => input.value)) period.value = 'custom';
  });
})();
