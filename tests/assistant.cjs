const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require('jsdom');
const source=fs.readFileSync('app/static/js/assistant.js','utf8');
let count=0;
function check(value,message){assert.ok(value,message);count++;}
function fixture({subscriptions=true,provider=true,storage,blocked=false,scope='public',storageScope=scope,links={}}={}){
 const config={subscriptions,provider,scope,links:{help:'/help',signup:'/auth/signup',reset:'/auth/forgot-password',plans:'/plans/',products:'/products/',inventory:'/products/?view=inventory',sales:'/sales/',restocking:'/restocking/',expenses:'/expenses/',reports:'/reports',businesses:'/businesses/',notifications:'/notifications/',...links}};
 const dom=new JSDOM(`<body><form id="business"><input name="submission_token" value="original"><button type="submit" disabled>Save</button></form><button id="menuButton">Menu</button><div data-assistant hidden><button class="sb-assistant-launcher" aria-expanded="false">Help</button><section class="sb-assistant-panel" hidden><button data-assistant-close>Close</button><button data-assistant-minimize>Minimize</button><div class="sb-assistant-history"></div><div class="sb-assistant-suggestions"></div><input maxlength="300"><button data-assistant-send>Send</button><button data-assistant-clear>Clear</button><span role="status"></span></section><script data-assistant-config type="application/json">${JSON.stringify(config)}</script></div></body>`,{url:'https://offline.invalid',runScripts:'outside-only'});
 const {window:w}=dom;
 let requests=0;w.fetch=()=>{requests++;throw Error('Forbidden network');};w.XMLHttpRequest=()=>{requests++;throw Error('Forbidden network');};
 if(storage!==undefined)w.sessionStorage.setItem('stockbridge:assistant:v1:'+storageScope,storage);
 if(blocked)Object.defineProperty(w,'sessionStorage',{get(){throw Error('Unavailable');}});
 w.eval(source);
 const root=w.document.querySelector('[data-assistant]'),panel=root.querySelector('section'),input=root.querySelector('input');
 const ask=text=>{input.value=text;root.querySelector('[data-assistant-send]').click();return root.querySelector('.sb-assistant-history').textContent;};
 return {dom,w,root,panel,input,ask,requests:()=>requests};
}
const cases=[['what does StockBridge do','connects products'],['which businesses can use it','goods sold by quantity'],['verify my email','verification'],['forgot password','password recovery'],['free trial','7-day'],['basic price','₦3,000'],['add product','purchase cost'],['opening stock','quantity you already have'],['how do I sell','sales basket'],['add stock','Restocking'],['damaged','stock adjustment'],['rent','operating costs'],['profit','Gross profit'],['two businesses','Working in'],['low stock','Notification Centre'],['saving timed out','transaction history'],['support','contact details remain pending']];
for(const [question,expected] of cases){const f=fixture();check(f.ask(question).includes(expected),question);check(f.requests()===0);f.dom.window.close();}
(async()=>{
 let f=fixture();
 check(f.root.hidden===false);f.root.querySelector('.sb-assistant-launcher').click();check(!f.panel.hidden);check(f.w.document.activeElement===f.input);
 f.ask('sales and expenses');check(f.root.querySelector('.sb-assistant-history').textContent.includes('Which topic'));
 check(f.root.querySelector('.sb-assistant-suggestions').children.length===2);
 f.root.querySelector('.sb-assistant-suggestions button').click();check(f.w.sessionStorage.length===1);
 const stored=f.w.sessionStorage.getItem('stockbridge:assistant:v1:public');check(JSON.parse(stored).topics[0]==='sales');
 f.ask('<img src=x onerror=alert(1)> private-password-123');check(!f.root.querySelector('.sb-assistant-history img'));check(!f.w.sessionStorage.getItem('stockbridge:assistant:v1:public').includes('private-password'));
 check(f.root.querySelector('.sb-assistant-history').textContent.includes('couldn’t confidently match'));
 for(let i=0;i<15;i++)f.ask('product');check(JSON.parse(f.w.sessionStorage.getItem('stockbridge:assistant:v1:public')).topics.length===10);
 check(f.root.querySelector('.sb-assistant-history').children.length<=24);
 f.input.dispatchEvent(new f.w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));check(f.panel.hidden);check(f.w.document.activeElement===f.root.querySelector('.sb-assistant-launcher'));
 f.root.querySelector('.sb-assistant-launcher').click();f.root.querySelector('[data-assistant-minimize]').click();check(f.panel.hidden);
 f.root.querySelector('.sb-assistant-launcher').click();f.w.document.body.classList.add('navigation-open');await Promise.resolve();check(f.panel.hidden);check(f.w.document.activeElement!==f.root.querySelector('.sb-assistant-launcher'));
 f.w.document.body.classList.remove('navigation-open');f.w.document.querySelector('[name=submission_token]').focus();check(f.root.hasAttribute('data-field-focus'));
 check(f.w.document.querySelector('[name=submission_token]').value==='original');check(f.w.document.querySelector('#business button').disabled);check(f.requests()===0);
 f.root.querySelector('[data-assistant-clear]').click();check(f.w.sessionStorage.length===0);check(!f.root.querySelector('.sb-assistant-history').textContent.includes('private-password'));f.dom.window.close();
 f=fixture({storage:stored,subscriptions:false});check(f.root.textContent.includes('sales basket'));check(f.ask('trial').includes('rollout is currently disabled'));check(f.ask('prices').includes('₦50,000'));check(f.root.textContent.includes('checkout is not currently available'));f.dom.window.close();
 f=fixture({provider:false});check(f.ask('plans').includes('Billing-provider rollout is disabled'));f.dom.window.close();
 f=fixture({storage:stored,scope:'customer',storageScope:'public'});check(!f.root.textContent.includes('sales basket'));f.dom.window.close();
 for(const storage of ['bad',JSON.stringify({v:1,at:Date.now()-1800001,topics:['sales']}),JSON.stringify({v:2,at:Date.now(),topics:['sales']}),JSON.stringify({v:1,at:Date.now(),topics:['evil']}),JSON.stringify({v:1,at:Date.now()+60000,topics:['sales']}),JSON.stringify({v:1,at:Date.now(),topics:Array(11).fill('sales')})]){
  f=fixture({storage});check(!f.root.textContent.includes('sales basket'));check(f.w.sessionStorage.length===0);f.dom.window.close();
 }
 f=fixture({blocked:true});check(f.ask('sale').includes('sales basket'));f.dom.window.close();
 f=fixture({links:{sales:'//evil.invalid'}});f.ask('sale');check(!f.root.querySelector('.sb-assistant-history a'));f.dom.window.close();
 f=fixture();let submitted=false;f.w.document.querySelector('#business').addEventListener('submit',()=>submitted=true);f.input.value='sale';f.input.dispatchEvent(new f.w.KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));check(f.root.textContent.includes('sales basket'));check(!submitted);f.dom.window.close();

 f=fixture();
 f.w.document.body.classList.add('customer-app');
 const aside=f.w.document.createElement('aside');aside.id='sidebar';aside.innerHTML='<button id="sidebarClose">Close navigation</button><a href="/products/">Products</a><button>Logout</button>';f.w.document.body.append(aside);
 const main=f.w.document.createElement('main');main.className='main-panel';f.w.document.body.append(main);
 f.w.matchMedia=()=>({matches:true,addEventListener(){}});
 f.w.eval(fs.readFileSync('app/static/js/app.js','utf8'));
 f.w.eval(fs.readFileSync('app/static/js/customer.js','utf8'));
 f.root.querySelector('.sb-assistant-launcher').click();
 f.w.document.querySelector('#menuButton').click();await Promise.resolve();
 check(f.panel.hidden);check(aside.classList.contains('open'));check(main.inert===true);check(f.w.document.activeElement===aside.querySelector('button'));
 aside.querySelector('button').click();await Promise.resolve();check(!aside.classList.contains('open'));check(f.w.document.activeElement.id==='menuButton');
 f.root.querySelector('.sb-assistant-launcher').click();f.ask('sale');check(!f.w.document.querySelector('#business').hasAttribute('data-submitting'));check(f.w.document.querySelector('#business button').disabled);f.dom.window.close();
 console.log(`PASS: ${count} assistant assertions`);
})().catch(error=>{console.error(error);process.exitCode=1;});
