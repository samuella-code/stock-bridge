'use strict';
document.addEventListener('click', async event => {
  const check = event.target.closest('[data-check-payment]');
  if (check) {
    const result = check.parentElement.querySelector('[data-check-result]');
    check.disabled = true;
    result.textContent = 'Checking with Paystack…';
    try {
      const response = await fetch(check.dataset.checkPayment, {
        method: 'POST', credentials: 'same-origin',
        headers: {'Accept': 'application/json', 'Content-Type': 'application/json',
          'X-CSRFToken': document.querySelector('meta[name="csrf-token"]').content},
        body: '{}'
      });
      if (!response.headers.get('content-type')?.includes('application/json')) {
        throw new Error('Refresh the page and sign in again before checking.');
      }
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'The payment could not be checked.');
      result.textContent = data.message;
    } catch (error) {
      result.textContent = error.message;
    } finally {
      check.disabled = false;
    }
    return;
  }
  const button = event.target.closest('[data-copy-reference]');
  if (!button) return;
  const status = document.getElementById('payment-copy-status');
  try {
    await navigator.clipboard.writeText(button.dataset.copyReference);
    status.textContent = 'Payment reference copied.';
  } catch (_) {
    status.textContent = 'Copy is unavailable in this browser. Open payment details to select the full reference.';
  }
});
