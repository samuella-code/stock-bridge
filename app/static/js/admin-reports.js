'use strict';
const reportDateLink = document.querySelector('[data-pick-dates]');
if (reportDateLink) reportDateLink.addEventListener('click', event => {
  event.preventDefault();
  const dates = document.getElementById('report-custom-dates');
  dates.hidden = !dates.hidden;
  reportDateLink.setAttribute('aria-expanded', String(!dates.hidden));
  if (!dates.hidden) dates.querySelector('input[name="start"]').focus();
});
const reportPreference = document.querySelector('.report-preference-form');
if (reportPreference) {
  reportPreference.querySelector('[data-save-preference]').hidden = true;
  reportPreference.querySelector('[role="switch"]').addEventListener('change', () => reportPreference.requestSubmit());
}
document.addEventListener('keydown', event => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
    const search = document.getElementById('platform-search');
    if (search) { event.preventDefault(); search.focus(); }
  }
});
