(() => {
 const dialog=document.querySelector('#stockbridge-tour');if(!dialog||typeof dialog.showModal!=='function')return;
 let state=JSON.parse(document.querySelector('#tour-state').textContent),busy=false,highlight,openedMenu=false;
 const links=JSON.parse(document.querySelector('#tour-links').textContent),sidebar=document.querySelector('#sidebar'),menu=document.querySelector('#menuButton');
 const title=dialog.querySelector('#tour-title'),copy=dialog.querySelector('#tour-copy'),counter=dialog.querySelector('#tour-counter'),error=dialog.querySelector('#tour-error');
 const controls=Object.fromEntries(['start','back','next','finish','skip'].map(key=>[key,dialog.querySelector(`[data-tour-${key}]`)]));
 const steps=[['Dashboard','See your business overview, sales, expenses, profit and low-stock alerts.'],['Products','Add products with their selling prices, units and existing quantities as opening stock.'],['Inventory','Track stock automatically as sales and restocks happen, and review low-stock warnings.'],['Sales','Record genuine sales. Stock reduces automatically when the transaction is saved.'],['Restocking','Record purchased stock and unit costs. Saved restocks increase stock automatically.'],['Expenses','Record genuine operating costs such as rent, transport and electricity.'],['Reports','Review revenue, expenses, profit and recorded business performance.']];
 function mobile(){return window.matchMedia('(max-width:820px)').matches;}
 function clean(){highlight?.classList.remove('tour-highlight');if(openedMenu&&sidebar?.classList.contains('open'))menu?.click();openedMenu=false;}
 function exit(){clean();dialog.close();if(mobile())menu?.focus();else document.querySelector('.sidebar-nav a')?.focus();}
 function draw(){
  highlight?.classList.remove('tour-highlight');
  controls.start.hidden=state.started;controls.back.hidden=!state.started||state.step===0;controls.next.hidden=!state.started||state.step===6;controls.finish.hidden=!state.started||state.step!==6;
  if(!state.started){title.textContent='Welcome to StockBridge!';copy.textContent="Let's take a quick tour to show you how to manage your products, sales, inventory and business finances.";counter.textContent='';}
  else{
   if(mobile()&&menu&&!sidebar.classList.contains('open')){menu.click();openedMenu=true;}
   highlight=[...document.querySelectorAll('.sidebar-nav a')].find(link=>link.getAttribute('href')===links[state.step]);
   if(!highlight){error.textContent='Navigation is unavailable. Close this window and return to the dashboard.';controls.next.disabled=true;controls.finish.disabled=true;return;}
   highlight.classList.add('tour-highlight');highlight.scrollIntoView({block:mobile()?'center':'nearest',behavior:'auto'});
   title.textContent=steps[state.step][0];copy.textContent=steps[state.step][1];counter.textContent=`Step ${state.step+1} of 7`;
  }
  title.focus();
 }
 async function save(action,step){if(busy)return;busy=true;Object.values(controls).forEach(button=>button.disabled=true);error.textContent='';
  try{
   const response=await fetch(dialog.dataset.endpoint,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':document.querySelector('meta[name=csrf-token]').content},body:JSON.stringify({action,step,version:state.version})});
   if(!response.ok)throw Error();state=await response.json();if(!state.eligible){exit();return;}
  }catch(_){error.textContent='Could not save tour progress. Try again, or close this window and refresh the dashboard.';}
  finally{busy=false;Object.values(controls).forEach(button=>button.disabled=false);if(dialog.open)draw();}
 }
 controls.start.onclick=()=>save('start',0);controls.next.onclick=()=>save('progress',state.step+1);controls.back.onclick=()=>save('progress',state.step-1);controls.skip.onclick=()=>save('skip',state.step);controls.finish.onclick=()=>save('finish',6);
 dialog.addEventListener('cancel',event=>{event.preventDefault();exit();});
 dialog.addEventListener('keydown',event=>{
  if(event.key==='Escape'){event.preventDefault();event.stopPropagation();exit();}
  if(event.key==='Tab'){event.stopPropagation();const buttons=Object.values(controls).filter(button=>!button.hidden&&!button.disabled),first=buttons[0],last=buttons[buttons.length-1];if(event.shiftKey&&(document.activeElement===first||document.activeElement===title)){event.preventDefault();last?.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}}
 });
 window.addEventListener('pagehide',clean);
 window.matchMedia('(max-width:820px)').addEventListener?.('change',()=>{if(dialog.open)draw();});
 dialog.hidden=false;dialog.showModal();draw();
})();
