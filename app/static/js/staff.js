// Invitation secrets stay out of server URLs and browser storage.
(() => {
  const link = document.querySelector('[data-invite-link]');
  if (link?.value && /^https?:$/.test(window.location.protocol)) link.value = new URL(link.value, window.location.origin).href;
  const inviteToken = new URLSearchParams((window.location.hash || '').slice(1)).get('invite-token');
  if (inviteToken && /^[A-Za-z0-9_-]{43}$/.test(inviteToken) && link) {
    const endpoint = new URL(link.dataset.inviteEndpoint, window.location.origin);
    link.value = endpoint.href + '#token=' + inviteToken;
    document.querySelector('[data-staff-invite-success]')?.removeAttribute('hidden');
    document.querySelector('[data-copy-staff-invite]')?.removeAttribute('disabled');
    // Remove the secret from the address bar after the copy control is populated.
    window.history.replaceState(null, '', window.location.pathname + window.location.search);
  }
  const copyButton = document.querySelector('[data-copy-staff-invite]');
  copyButton?.addEventListener('click', async () => {
    const link = document.querySelector('[data-invite-link]');
    const status = document.querySelector('[data-copy-staff-invite-status]');
    if (!link?.value || !status) return;
    try {
      await navigator.clipboard.writeText(link.value);
      status.textContent = 'Link copied.';
    } catch {
      link.focus();
      link.select();
      status.textContent = 'Select and copy the invitation link.';
    }
  });
  const form = document.querySelector('[data-review-invite]');
  if (!form) return;
  const token = new URLSearchParams((window.location.hash || '').slice(1)).get('token');
  window.history.replaceState(null, '', window.location.pathname);
  if (token && /^[A-Za-z0-9_-]{43}$/.test(token)) {
    form.elements.namedItem('token').value = token;
    form.querySelector('button').disabled = false;
  }
})();
