'use strict';
const statusForm = document.querySelector('.business-status-form');
if (statusForm) {
  const field = statusForm.elements.business_name;
  const submit = statusForm.querySelector('[data-status-submit]');
  const update = () => { submit.disabled = field.value !== statusForm.dataset.businessName; };
  field.addEventListener('input', update);
  // A cached/back-navigation form may already contain the name.
  update();
  statusForm.addEventListener('submit', event => {
    update();
    if (submit.disabled) { event.preventDefault(); field.focus(); }
  });
}
