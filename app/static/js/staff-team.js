(() => {
  const tabs = [...document.querySelectorAll('[data-team-tabs] a')];
  const panels = [...document.querySelectorAll('[data-team-panel]')];
  function remember(hash) { try { history.replaceState(null,'',hash); } catch { /* Disconnected previews can restrict history. */ } }
  function activate(id, focus = false) {
    if (!panels.some(panel => panel.id === id)) id = panels[0]?.id;
    tabs.forEach(tab => {
      const selected = tab.hash === `#${id}`;
      tab.setAttribute('aria-selected', String(selected));tab.tabIndex = selected ? 0 : -1;
      if (selected && focus) tab.focus();
    });
    panels.forEach(panel => {panel.hidden = panel.id !== id;});
  }
  if (tabs.length) {
    tabs[0].parentElement.setAttribute('role','tablist');
    tabs.forEach((tab,index) => {
      tab.setAttribute('role','tab');tab.id=`team-tab-${index}`;
      tab.setAttribute('aria-controls',tab.hash.slice(1));
      const panel=document.getElementById(tab.hash.slice(1));
      panel.setAttribute('role','tabpanel');panel.setAttribute('aria-labelledby',tab.id);panel.tabIndex=0;
      tab.addEventListener('click',event => {event.preventDefault();activate(tab.hash.slice(1));remember(tab.hash);});
      tab.addEventListener('keydown',event => {
        const key=event.key;let target=index;
        if (key==='ArrowRight') target=(index+1)%tabs.length;
        else if (key==='ArrowLeft') target=(index+tabs.length-1)%tabs.length;
        else if (key==='Home') target=0;
        else if (key==='End') target=tabs.length-1;
        else return;
        event.preventDefault();activate(tabs[target].hash.slice(1),true);remember(tabs[target].hash);
      });
    });
    activate(location.hash.slice(1));
    addEventListener('hashchange',()=>activate(location.hash.slice(1)));
  }
  document.querySelectorAll('[data-copy-team-invite]').forEach(copy => {
    copy.addEventListener('click',async () => {
      const input=document.querySelector('#team-invite-link');
      const status=copy.closest('.team-card')?.querySelector('[data-copy-status]');
      if (!input || !input.value) return;
      try {
        await navigator.clipboard.writeText(new URL(input.value,location.origin).href);
        status.textContent='Invite link copied. Only share it with the invited person.';
      } catch {
        input.type='text';input.focus();input.select();status.textContent='Select and copy the invitation link above.';
      }
    });
  });
})();
