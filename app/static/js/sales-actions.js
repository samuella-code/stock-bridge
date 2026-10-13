(() => {
 document.querySelectorAll('.sale-actions').forEach(actions=>{
  const open=actions.querySelector('[data-void-open]'),form=actions.querySelector('form'),summary=actions.querySelector('summary'),view=actions.querySelector('[data-sale-details]'),detail=actions.querySelector('[id^=sale-details-]');
  function close(){detail.hidden=true;view.setAttribute('aria-expanded','false');form.hidden=true;open.setAttribute('aria-expanded','false');actions.open=false;summary.focus();}
  view.addEventListener('click',()=>{detail.hidden=!detail.hidden;view.setAttribute('aria-expanded',String(!detail.hidden));form.hidden=true;open.setAttribute('aria-expanded','false');});
  open.addEventListener('click',()=>{detail.hidden=true;view.setAttribute('aria-expanded','false');form.hidden=false;open.setAttribute('aria-expanded','true');form.querySelector('[name=reason]').focus();});
  actions.querySelector('[data-void-cancel]').addEventListener('click',close);
  actions.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();close();}});
  actions.addEventListener('toggle',()=>{if(!actions.open){detail.hidden=true;view.setAttribute('aria-expanded','false');form.hidden=true;open.setAttribute('aria-expanded','false');}});
 });
})();
