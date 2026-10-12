/* Browser-only, predefined guided help. No provider, account lookup or transaction action. */
(() => {
  const root = document.querySelector('[data-assistant]');
  if (!root) return;
  let config;
  try { config = JSON.parse(root.querySelector('[data-assistant-config]').textContent); }
  catch (_) { return; }
  const catalogue = [
    ['overview','What does StockBridge do?', ['what is stockbridge','what does stockbridge do','features','how does stockbridge work'], 'StockBridge connects products, inventory, sales, restocking, operating expenses, dashboard and reports. It helps you understand your recorded business activity.', 'help','getting-started'],
    ['business-types','Which businesses can use StockBridge?', ['business types','which businesses','who can use','tailor','cobbler','retail','shop','fashion'], 'StockBridge is best suited to businesses tracking goods sold by quantity, such as mini marts, fashion shops and other retailers. Service businesses can use relevant recordkeeping tools, but there are no job-management, manufacturing or automatic raw-material consumption workflows.', 'help','getting-started'],
    ['registration','How do I create an account?', ['register','registration','sign up','signup','create account','create an account','email verification','verify email','verify my email','verification'], 'Create an account and verify your email using the verification message. Sign in to review verification instructions; do not share verification links or tokens here.', 'signup'],
    ['account-access','How do I recover account access?', ['forgot password','reset password','reset my password','login','log in','sign in','cannot access','cant access'], 'Use Sign In to access your account. If you forgot your password, use password recovery. Never enter your password, reset token or verification token in this assistant.', 'reset'],
    ['trial','Is there a free trial?', ['free trial','trial','seven days','7 days','7 day','free account'], () => config.subscriptions === true ? 'Eligible verified accounts can start a 7-day free trial under the existing account rules. No card is required to start and the trial does not automatically charge you. Not every account is eligible; this assistant cannot check your account.' : 'Subscription rollout is currently disabled here. Create and verify an account to explore StockBridge; do not assume a free trial or subscription checkout is currently available.', 'plans'],
    ['plans','What are the Basic and Plus prices?', ['price','prices','pricing','cost','basic','plus','plan','plans','subscription','pay','payment'], () => 'Basic: ₦3,000 monthly or ₦30,000 yearly, for one business. Plus: ₦5,000 monthly or ₦50,000 yearly, for up to two businesses. ' + (config.subscriptions !== true ? 'Subscription checkout is not currently available here.' : config.provider !== true ? 'Billing-provider rollout is disabled here; subscription checkout is not currently available.' : 'Review Billing & Plan for current checkout availability and your account options. This assistant cannot verify payment-provider readiness or account eligibility.'), 'plans'],
    ['products','How do I manage products?', ['product','products','catalogue','catalog','add item','selling price'], 'Add a clear product name, unit, purchase cost, selling price and low-stock threshold. Search and filter Products to find items. Use product details to review history; mark products inactive when appropriate.', 'products'],
    ['inventory','How do I enter opening stock?', ['opening stock','initial stock','current stock','inventory','stock quantity','quantity remaining'], 'Opening stock is the quantity you already have when registering a product. Sales reduce stock and restocking increases it. After registration, use stock adjustments to correct quantities rather than directly editing current stock.', 'inventory'],
    ['sales','How do I record a sale?', ['sale','sales','sell','sold','record sale','record a sale'], 'Search for products, add them to the sales basket, review quantities and selling prices, then submit once. JavaScript is required. Available stock limits what you can sell; check history before retrying after an uncertain response.', 'sales'],
    ['restocking','How do I restock products?', ['restock','restocking','received goods','purchase stock','buy stock','add stock','supplier'], 'Record goods received for resale in Restocking. Add products to the basket, enter quantities and purchase costs per unit, and include supplier details or notes where useful. Review before submitting once; history preserves purchase costs.', 'restocking'],
    ['adjustments','How do I adjust stock?', ['stock adjustment','stock adjustments','adjust stock','damaged','expired','lost stock','stock correction','returns'], 'Open a product’s details and use its stock adjustment action. Choose increase or decrease, enter the quantity and a genuine reason and notes. Adjustments cannot make stock negative and remain in stock history.', 'help','adjustments'],
    ['expenses','How do I record expenses?', ['expense','expenses','rent','electricity','transport','operating cost'], 'Record genuine operating costs with the correct amount, category and date. Goods bought for resale belong in Restocking. Check expense history before recording an expense again.', 'expenses'],
    ['reports','How do I read the dashboard and reports?', ['dashboard','report','reports','profit','revenue','chart','filter','custom dates'], 'Choose Today, Week, Month or Custom, then select Apply. Enter both dates for Custom. Dashboard summary cards follow the selected period; the revenue chart shows the last seven days. Gross profit subtracts recorded product costs from revenue; net profit also subtracts operating expenses.', 'reports'],
    ['businesses','How do I switch businesses?', ['multiple businesses','two businesses','2 businesses','second business','switch business','switch between businesses','active business','my businesses'], 'Where your account plan permits it, manage businesses in My Businesses and switch using Working in. Basic supports one business; Plus supports up to two. Records are separate. Check the active business before recording activity.', 'businesses'],
    ['notifications','How do notifications work?', ['notification','notifications','low stock','out of stock','stock alert','email alert','restocking email'], 'The Notification Centre shows business activity and supports marking notifications read. Preferences distinguish product-added and sales in-app notifications from low-stock, out-of-stock and restocking email alerts. Email delivery depends on configured services and account restrictions; this assistant cannot check delivery.', 'notifications'],
    ['interrupted','What if saving was interrupted?', ['interrupted','timeout','timed out','duplicate','submission','retry','saved twice','save failed','uncertain'], 'Check the relevant transaction history before recording anything again. Same-reference, unchanged retries are protected against duplication; changed contents under a used reference are rejected. If the form expires or the outcome is uncertain, check history before refreshing for a new transaction. This assistant cannot confirm whether saving succeeded.', 'help','interrupted'],
    ['help','Where can I find help?', ['help','support','contact','guide','guides'], 'The Help Centre contains practical guides for StockBridge. Official contact details remain pending approval; there is no support submission service in this assistant.', 'help']
  ];
  const topics = new Map(catalogue.map(([id,label,phrases,answer,link,anchor]) => [id,{id,label,phrases,answer,link,anchor}]));
  const normalize = text => text.toLowerCase().replace(/[^a-z0-9]+/g,' ').trim();
  function match(text) {
    const normalized = ' ' + normalize(text) + ' ';
    const scored = catalogue.map(([id,,phrases]) => ({id,score:Math.max(0,...phrases.map(phrase => normalized.includes(' '+phrase+' ') ? phrase.split(' ').length : 0))}));
    const best = Math.max(...scored.map(item => item.score));
    return best ? scored.filter(item => item.score === best).map(item => item.id) : [];
  }
  const panel=root.querySelector('.sb-assistant-panel'), launcher=root.querySelector('.sb-assistant-launcher');
  const input=root.querySelector('input'), history=root.querySelector('.sb-assistant-history');
  const suggestions=root.querySelector('.sb-assistant-suggestions'), status=root.querySelector('[role=status]');
  const storageKey='stockbridge:assistant:v1:'+(config.scope==='customer'?'customer':'public');
  const LIMIT=10, TTL=30*60*1000;
  let ids=[];
  function persist() {
    try { window.sessionStorage.setItem(storageKey,JSON.stringify({v:1,at:Date.now(),topics:ids.slice(-LIMIT)})); } catch (_) { /* In-memory help remains available. */ }
  }
  function restore() {
    try {
      const raw=window.sessionStorage.getItem(storageKey);
      if (!raw) return;
      if (raw.length>1500) throw new Error('Invalid state');
      const saved=JSON.parse(raw);
      if(saved.v!==1 || !Number.isFinite(saved.at) || Date.now()-saved.at>TTL || saved.at>Date.now() || !Array.isArray(saved.topics) || saved.topics.length>LIMIT || saved.topics.some(id=>typeof id!=='string'||!topics.has(id))) throw new Error('Invalid state');
      ids=saved.topics;
    } catch (_) { try { window.sessionStorage.removeItem(storageKey); } catch (_) {} }
  }
  function message(speaker,text,topic) {
    const item=document.createElement('div');item.className='sb-assistant-message';item.dataset.speaker=speaker;
    const label=document.createElement('strong');label.textContent=speaker==='user'?'You':'StockBridge Assistant';
    const p=document.createElement('p');p.textContent=text;item.append(label,p);
    if(topic){
      const href=config.links?.[topic.link];
      // Accept only fixed same-origin application paths, never browser input or storage URLs.
      if(typeof href==='string' && /^\/(?!\/)/.test(href) && !/[\\\s]/.test(href)) {
        const a=document.createElement('a');a.href=href+(topic.anchor?'#'+topic.anchor:'');a.textContent='Open '+(topic.link==='help'?'Help Centre guide':topic.link==='plans'?'Billing & Plan':topic.link);item.append(a);
      }
    }
    history.append(item);
    while(history.children.length>24) history.firstElementChild.remove();
    history.scrollTop=history.scrollHeight;
  }
  function choices(values) {
    suggestions.replaceChildren();
    values.forEach(id=>{const button=document.createElement('button');button.type='button';button.textContent=topics.get(id).label;button.addEventListener('click',()=>{respond(id,true);input.focus();});suggestions.append(button);});
  }
  function defaults(){choices(['overview','inventory','sales','plans']);}
  function answer(topic){return typeof topic.answer==='function'?topic.answer():topic.answer;}
  function respond(id,withQuestion){
    const topic=topics.get(id);if(!topic)return;
    if(withQuestion)message('user',topic.label);
    message('assistant',answer(topic),topic);ids.push(id);ids=ids.slice(-LIMIT);persist();defaults();
    status.textContent=answer(topic);
  }
  function resetView(){history.replaceChildren();message('assistant','Welcome! Ask about StockBridge or choose a question below. I provide predefined guided help and cannot access your records or perform transactions.');ids.forEach(id=>{const t=topics.get(id);message('user',t.label);message('assistant',answer(t),t);});defaults();}
  function send(){
    const text=input.value.trim().slice(0,300);if(!text)return;
    input.value='';message('user',text);
    const matches=match(text);
    if(matches.length===1)respond(matches[0],false);
    else if(matches.length>1){const reply='Which topic did you mean? Choose a question below.';message('assistant',reply);choices(matches);status.textContent=reply;}
    else {const reply='I couldn’t confidently match that question. Choose a topic or visit the Help Centre. Please don’t share passwords, payment details or private business information.';message('assistant',reply,topics.get('help'));defaults();status.textContent=reply;}
    input.focus();
  }
  function setOpen(open,restoreFocus=true){
    if(document.body.classList.contains('navigation-open'))open=false;
    panel.hidden=!open;launcher.hidden=open;launcher.setAttribute('aria-expanded',String(open));
    if(open)input.focus();else if(restoreFocus)launcher.focus();
  }
  launcher.addEventListener('click',()=>setOpen(true));
  root.querySelector('[data-assistant-close]').addEventListener('click',()=>setOpen(false));
  root.querySelector('[data-assistant-minimize]').addEventListener('click',()=>setOpen(false));
  root.querySelector('[data-assistant-clear]').addEventListener('click',()=>{ids=[];try{window.sessionStorage.removeItem(storageKey);}catch(_){}resetView();status.textContent='Conversation cleared.';input.focus();});
  root.querySelector('[data-assistant-send]').addEventListener('click',send);
  input.addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.isComposing){event.preventDefault();send();}});
  root.addEventListener('keydown',event=>{if(event.key==='Escape'&&!panel.hidden){event.preventDefault();setOpen(false);}});
  const body=document.body;
  const observer=new MutationObserver(()=>{if(body.classList.contains('navigation-open'))setOpen(false,false);});
  observer.observe(body,{attributes:true,attributeFilter:['class']});
  document.addEventListener('focusin',event=>{root.toggleAttribute('data-field-focus',!root.contains(event.target)&&event.target.matches('input,textarea,select'));});
  function viewport(){if(window.visualViewport){root.toggleAttribute('data-compact-height',window.visualViewport.height<440);root.style.setProperty('--assistant-viewport-height',window.visualViewport.height+'px');root.style.bottom=Math.max(12,window.innerHeight-window.visualViewport.height-window.visualViewport.offsetTop+12)+'px';}}
  window.visualViewport?.addEventListener('resize',viewport);window.visualViewport?.addEventListener('scroll',viewport);viewport();
  window.addEventListener('pageshow',()=>{if(document.body.classList.contains('navigation-open'))setOpen(false,false);});
  restore();resetView();root.hidden=false;document.body.classList.add('sb-assistant-enabled');
})();
