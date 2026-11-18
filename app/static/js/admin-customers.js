'use strict';
(() => {
  const drawer = document.getElementById('customer-drawer');
  if (!drawer) return;
  const actionDialog = document.getElementById('customer-action');
  const drawerBody = document.getElementById('customer-drawer-body');
  const actionForm = document.getElementById('customer-action-form');
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  const badge = value => `<span class="badge">${escape(value)}</span>`;
  const money = value => new Intl.NumberFormat('en-NG', {style:'currency', currency:'NGN', minimumFractionDigits: value % 100 ? 2 : 0}).format(value / 100);
  let drawerSequence = 0;
  let invitePage = 1;
  let invitationSequence = 0;

  async function request(url, values) {
    const options = {headers:{Accept:'application/json'}, credentials:'same-origin'};
    if (values !== undefined) {
      options.method = 'POST';
      options.headers['X-CSRFToken'] = csrf;
      if (values instanceof FormData) {
        options.body = values;
      } else {
        options.headers['Content-Type'] = 'application/json';
        options.body = JSON.stringify(values);
      }
    }
    const response = await fetch(url, options);
    const json = response.headers.get('content-type')?.includes('application/json');
    const result = json ? await response.json() : null;
    if (!response.ok) throw new Error(result?.error || (response.status === 409 ? 'The record changed. Reload and try again.' : 'The request could not be completed. Refresh the page and try again.'));
    if (!result && !(values instanceof FormData)) {
      throw new Error('Your admin session may have expired. Refresh and sign in again.');
    }
    return result;
  }

  function status(message, error = false) {
    const element = document.getElementById(actionDialog.open ? 'action-status' : drawer.open ? 'drawer-status' : 'customer-status-message');
    element.textContent = message;
    element.classList.toggle('error', error);
  }

  function note(c) {
    return `<section class="card note" data-note-user="${c.id}" data-note-version="${c.note.version}"><label for="drawer-note">Internal note</label><p class="caption muted">Visible only to your admin team.</p><textarea id="drawer-note" maxlength="5000">${escape(c.note.body)}</textarea><button class="btn" type="button" data-save-note>Save note</button></section>`;
  }

  function staffActions(i) {
    return ['Invited','Expired'].includes(i.status) ? `<button class="text-btn" type="button" data-staff-action="resend" data-invitation-id="${i.id}">Resend</button><button class="text-btn" type="button" data-staff-action="cancel" data-invitation-id="${i.id}">Cancel</button>` : '';
  }

  function teamEntry(i) {
    return `<div class="team-item"><span data-email-value>${escape(i.email)}</span><button class="text-btn" type="button" data-reveal-url="/admin/staff-invitations/${i.id}/email">Show full email</button><p class="caption muted">${escape(i.role)} · ${escape(i.businesses.map(b=>b.name).join(', '))}</p>${badge(i.status)}${staffActions(i)}</div>`;
  }

  function customerActions(c) {
    const owner = c.employee ? '' : `<button class="btn" type="button" data-customer-action="message">Send message</button>${c.trial_end && !c.interval ? '<button class="btn" type="button" data-customer-action="trial">Extend trial</button>' : ''}<button class="btn danger" type="button" data-customer-action="suspension">${c.suspended ? 'Reactivate' : 'Suspend'}</button>`;
    return `<div class="actions spaced" data-customer-id="${c.id}">${owner}<a class="btn" href="/admin/users/${c.id}/read-only">View as read-only</a></div>`;
  }

  function renderDrawer(c) {
    const team = c.employee ? c.works_for.map(m=>`<div class="team-item">
<strong>${escape(m.owner)}</strong>
<p>${escape(m.role)} · ${escape(m.businesses.map(b=>b.name).join(', '))}</p>${badge(m.status)}${m.status !== 'Removed' ? `<button class="text-btn" type="button" data-remove-membership="${m.id}">Remove access</button>` : ''}</div>`).join('') : c.team.map(teamEntry).join('');
    return `<section class="card">
<div class="profile-top">
<span class="avatar large" aria-hidden="true">${escape(c.name.slice(0,1).toUpperCase())}</span>
<div>
<h2>${escape(c.name)}</h2>${badge(c.employee?'Employee':'Account owner')}</div>
</div>
<div class="email-line">
<span data-email-value>${escape(c.email)}</span>
<button class="text-btn" type="button" data-reveal-url="/admin/users/${c.id}/email">Show</button>
</div>
<div class="badges">${badge(c.plan)}${badge(c.verified?'Verified':'Email unverified')}</div>
</section>
<section class="card">
<h3>${c.employee?'Works for':'Businesses'}</h3>${c.employee?'<p class="muted spaced">Covered by owner’s plan. Plan reminders are never sent to employees.</p>':c.businesses.map(b=>`<div class="business-item">
<strong>${escape(b.name)}</strong>
<a class="text-btn" href="/admin/businesses/${b.id}">Open →</a>
</div>`).join('')||'<p class="muted spaced">No businesses registered.</p>'}</section>
<section class="card">
<h3>${c.employee?'Assignments':'Team'}</h3>${team||'<p class="muted spaced">Invited employees will appear here.</p>'}</section>${c.employee?'':`<div class="mini-stats">
<section class="card">
<span>Paid so far</span>
<strong>${escape(money(c.paid_so_far_kobo))}</strong>
</section>
<section class="card">
<span>Staff seats</span>
<strong>${c.staff_seats.used} of ${c.staff_seats.limit}</strong>
</section>
</div>`}<section class="card">
<h3>Actions</h3>${customerActions(c)}</section>${note(c)}<a class="btn primary" href="/admin/users/${c.id}">Open customer details →</a>`;
  }

  async function openCustomer(id) {
    const sequence = ++drawerSequence;
    drawerBody.innerHTML = '<p>Loading customer…</p>';
    document.getElementById('drawer-status').textContent = '';
    if (!drawer.open) drawer.showModal();
    try {
      const c = await request(`/admin/users/${id}/data`);
      if (sequence === drawerSequence && drawer.open) drawerBody.innerHTML = renderDrawer(c);
    } catch (error) {
      if (sequence === drawerSequence) status(error.message, true);
    }
  }

  function field(name, label, input) {
    return `<label>${escape(label)}${input || `<input name="${name}" required maxlength="300">`}</label>`;
  }

  async function showAction(button) {
    const kind = button.dataset.customerAction || button.dataset.staffAction || 'remove';
    let url;
    let fields;
    let values;
    if (button.dataset.customerAction) {
      const id = button.closest('[data-customer-id]').dataset.customerId;
      const c = await request(`/admin/users/${id}/data`);
      url = `/admin/users/${id}/${kind}`;
      if (kind === 'message') {
        fields = field('subject','Subject','<input name="subject" required maxlength="150">') + field('body','Message','<textarea name="body" required maxlength="5000"></textarea>');
        values = {request_key:crypto.randomUUID()};
      } else if (kind === 'trial') {
        fields = field('days','Extra days','<input type="number" name="days" value="7" min="1" max="30" required>') + field('reason','Reason');
        values = {expected_end:c.trial_end};
      } else {
        fields = field('reason','Reason');
        url = `/admin/users/${id}/suspension`;
        values = {desired_state:c.suspended?'reactivate':'suspend', expected_state:c.suspended?'suspended':'active', expected_suspended_at:c.suspended_at};
      }
    } else {
      fields = field('reason','Reason');
      url = button.dataset.removeMembership ? `/admin/staff-memberships/${button.dataset.removeMembership}/remove` : `/admin/staff-invitations/${button.dataset.invitationId}/${kind}`;
      values = {};
    }
    document.getElementById('customer-action-title').textContent = ({message:'Send message',trial:'Extend trial',suspension:'Change account status',resend:'Resend invitation',cancel:'Cancel invitation',remove:'Remove employee access'})[kind];
    actionForm.innerHTML = fields + '<label><input type="checkbox" name="confirm" required>Confirm this action.</label><button class="btn primary" type="submit">Confirm</button>';
    document.getElementById('action-status').textContent = '';
    if (!actionDialog.open) actionDialog.showModal();
    actionForm.onsubmit = async event => {
      event.preventDefault();
      const submit = actionForm.querySelector('[type="submit"]');
      submit.disabled = true;
      try {
        const submitted = new FormData(actionForm);
        Object.entries(values).forEach(([key,value])=>submitted.set(key,value));
        submitted.set('csrf_token',csrf);
        const result = await request(url, submitted);
        if (kind === 'message' && result.status !== 'sent') {
          status(`Message delivery is ${result.status}. Delivery needs review before sending again.`,true);
          return;
        }
        if (kind === 'resend' && result.status !== 'sent') {
          status(`The invitation was replaced. Email delivery is ${result.status}.`,true);
          return;
        }
        location.reload();
      } catch (error) {
        status(error.message, true);
      } finally {
        submit.disabled = false;
      }
    };
  }

  async function loadInvitations() {
    if (!document.querySelector('[data-platform-invitations]')) return;
    const sequence = ++invitationSequence;
    const params = new URLSearchParams(new FormData(document.getElementById('invitation-filter')));
    params.set('page',invitePage);
    try {
      const result = await request('/admin/staff-invitations/data?'+params);
      if (sequence !== invitationSequence) return;
      document.getElementById('platform-invitation-rows').innerHTML = result.invitations.map(i=>`<tr><td data-label="Invited email"><span data-email-value>${escape(i.email)}</span><br><button class="text-btn" type="button" data-reveal-url="/admin/staff-invitations/${i.id}/email">Show full email</button></td><td data-label="Invited by">${escape(i.invited_by.name)}</td><td data-label="Business">${escape(i.businesses.map(b=>b.name).join(', '))}</td><td data-label="Role">${escape(i.role)}</td><td data-label="Sent">${escape(i.sent)}</td><td data-label="Status">${badge(i.status)}${staffActions(i)}</td><td data-label="Email delivery">${escape(i.email_delivery.replaceAll('_',' '))}</td></tr>`).join('')||'<tr><td data-label="Invitations" colspan="7">No matching invitations.</td></tr>';
      document.getElementById('invitation-pages').innerHTML = (result.page>1?'<button class="btn" type="button" data-invitation-page="-1">Previous</button>':'')+(result.page<result.pages?'<button class="btn" type="button" data-invitation-page="1">Next</button>':'');
      params.delete('page');
      document.getElementById('invitation-export').href='/admin/staff-invitations/export.csv?'+params;
    } catch (error) {
      status(error.message, true);
    }
  }

  document.addEventListener('click', async event => {
    const close = event.target.closest('[data-close-drawer],[data-close-action]');
    if (close) {
      if (close.hasAttribute('data-close-action')) {
        actionDialog.close();
      } else {
        ++drawerSequence;
        drawer.close();
      }
      return;
    }
    const reveal = event.target.closest('[data-reveal-url]');
    if (reveal) {
      reveal.disabled = true;
      try {
        const result = await request(reveal.dataset.revealUrl, {});
        reveal.parentElement.querySelector('[data-email-value]').textContent = result.email;
        reveal.textContent = 'Shown';
      } catch (error) {
        reveal.disabled = false;
        status(error.message, true);
      }
      return;
    }
    const save = event.target.closest('[data-save-note]');
    if (save) {
      const section = save.closest('[data-note-user]');
      save.disabled = true;
      try {
        const result = await request(`/admin/users/${section.dataset.noteUser}/note`, {
          body: section.querySelector('textarea').value,
          version: Number(section.dataset.noteVersion),
        });
        section.dataset.noteVersion = result.version;
        status('Internal note saved.');
      } catch (error) {
        status(error.message, true);
      } finally {
        save.disabled = false;
      }
      return;
    }
    const action = event.target.closest('[data-customer-action],[data-staff-action],[data-remove-membership]');
    if (action) {
      try {
        await showAction(action);
      } catch (error) {
        status(error.message, true);
      }
      return;
    }
    const page = event.target.closest('[data-invitation-page]');
    if (page) {
      invitePage += Number(page.dataset.invitationPage);
      await loadInvitations();
      return;
    }
    const target = event.target.closest('[data-open-customer]');
    const control = event.target.closest('a,button,details');
    if (target && (!control || control.hasAttribute('data-open-customer'))) {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      await openCustomer(target.dataset.openCustomer);
    }
  });
  document.getElementById('invitation-filter')?.addEventListener('submit', event => {
    event.preventDefault();
    invitePage = 1;
    loadInvitations();
  });
  loadInvitations();
})();
