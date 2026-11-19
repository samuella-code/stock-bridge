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

// Keep the existing local-format fields and server validation. The unnamed native
// picker only supplies a date; it never changes the form contract.
document.querySelectorAll('[data-report-calendar]').forEach(button => {
  const field = document.getElementById(button.dataset.reportCalendar);
  const picker = document.getElementById(field.id + '-calendar');
  if (typeof picker.showPicker !== 'function') return;
  button.hidden = false;
  button.addEventListener('click', () => {
    const parts = field.value.trim().match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
    picker.value = parts ? `${parts[3]}-${parts[2]}-${parts[1]}` : '';
    picker.showPicker();
  });
  picker.addEventListener('change', () => {
    if (!picker.value) return;
    const [year, month, day] = picker.value.split('-');
    field.value = `${day}/${month}/${year}`;
    field.dispatchEvent(new Event('input', {bubbles:true}));
    field.dispatchEvent(new Event('change', {bubbles:true}));
    button.focus();
  });
});
