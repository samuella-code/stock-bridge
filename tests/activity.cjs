// Presentation/control tests in a VM; not a rendered-browser viewport claim.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
let assertions = 0;
function equal(a,b) { assert.equal(a,b); assertions++; }
function ok(value) { assert.ok(value); assertions++; }
const source = fs.readFileSync('app/static/js/activity.js','utf8');
function mount(options={}) {
  const handlers={}, button={disabled:false}, error={textContent:''}, state={textContent:'Unread'}, classes=new Set(['notification-unread']);
  const row={classList:{remove(v){classes.delete(v);}},querySelector(){return state;}};
  const form={action:'/notifications/1/open', addEventListener(k,fn){handlers[k]=fn;},closest(){return row;},querySelector(s){return s==='button'?button:error;}};
  let badge={textContent:'3',remove(){badge=null;}};
  const bell={attributes:{},setAttribute(k,v){this.attributes[k]=v;},querySelector(){return badge;},appendChild(v){badge=v;}};
  let requests=0, destination=null, resolve;
  const document={querySelectorAll(){return [form];},querySelector(s){return s==='[data-notification-bell]'?bell:null;},createElement(){return {remove(){badge=null;}};}};
  const fetch=() => {requests++; return new Promise(r=>{resolve=r;});};
  const window={location:{origin:'https://stockbridge.example',assign(v){destination=v;}}};
  vm.runInNewContext(source,{document,window,fetch,FormData:function(){},URL});
  return {handlers,button,error,state,classes,bell, get badge(){return badge;},get requests(){return requests;},get destination(){return destination;},resolve(response){resolve(response);}};
}
(async()=>{
  let s=mount();let event={preventDefault(){}};
  const pending=s.handlers.submit(event);
  equal(s.requests,1);equal(s.button.disabled,true);equal(s.badge.textContent,'3');
  await s.handlers.submit(event);equal(s.requests,1);
  s.resolve({ok:true,json:async()=>({unread_count:2,destination:'/products/1'})});await pending;
  equal(s.badge.textContent,'2');equal(s.state.textContent,'Read');equal(s.classes.has('notification-unread'),false);equal(s.bell.attributes['aria-label'],'Notifications, 2 unread');equal(s.destination,'https://stockbridge.example/products/1');
  await s.handlers.submit(event);equal(s.requests,1);
  s=mount();let operation=s.handlers.submit(event);s.resolve({ok:false});await operation;
  equal(s.button.disabled,false);equal(s.badge.textContent,'3');equal(s.state.textContent,'Unread');equal(s.classes.has('notification-unread'),true);ok(s.error.textContent.includes('try again'));equal(s.destination,null);
  operation=s.handlers.submit(event);equal(s.requests,2);s.resolve({ok:true,json:async()=>({unread_count:0,destination:'/products/'})});await operation;equal(s.badge,null);
  for(const result of [{unread_count:-1,destination:'/products/'},{unread_count:2,destination:'https://evil.invalid/'}]) {
    s=mount();operation=s.handlers.submit(event);s.resolve({ok:true,json:async()=>result});await operation;
    equal(s.badge.textContent,'3');equal(s.state.textContent,'Unread');equal(s.destination,null);equal(s.button.disabled,false);
  }
  s=mount();operation=s.handlers.submit(event);s.resolve({ok:true,json:async()=>({unread_count:101,destination:'/products/'})});await operation;equal(s.badge.textContent,'99+');
  // Bounded dispatch drains committed jobs, but never busy-polls retries.
  let calls=0;
  const trigger={action:'/notifications/email-dispatch'};
  const document={querySelectorAll(){return [];},querySelector(){return trigger;}};
  vm.runInNewContext(source,{document,window:{},FormData:function(){},URL,fetch:async()=>({ok:true,json:async()=>{calls++;return {result:calls<3?'sent':'idle'};}})});
  await new Promise(r=>setImmediate(r));equal(calls,3);
  calls=0;
  vm.runInNewContext(source,{document,window:{},FormData:function(){},URL,fetch:async()=>({ok:true,json:async()=>{calls++;return {result:'sent'};}})});
  await new Promise(r=>setImmediate(r));equal(calls,10);
  console.log(`PASS: click/read badge confirmation, repeat-click protection, errors, destination checks and bounded dispatch (${assertions} assertions).`);
})().catch(error=>{console.error(error);process.exitCode=1;});
