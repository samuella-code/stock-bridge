const assert=require('node:assert/strict'),fs=require('node:fs'),{JSDOM}=require('jsdom');
let count=0;function check(value){assert.ok(value);count++;}
async function scenario(mobile,terminal){
 const urls=['/dashboard','/products/','/products/?view=inventory&sort=priority','/sales/','/restocking/','/expenses/','/reports'];
 const template=fs.readFileSync('app/templates/components/tour.html','utf8');
 const markup=template.replace(/{{url_for\('main.onboarding_tour_state'\)}}/g,'/onboarding/tour').replace(/{{onboarding_tour\|tojson}}/g,JSON.stringify({eligible:true,started:false,step:0,version:0}));
 const cut=markup.indexOf('<script type="application/json" id="tour-links">');
 const html=markup.slice(0,cut)+`<script type="application/json" id="tour-links">${JSON.stringify(urls)}</script></dialog>`;
 const dom=new JSDOM(`<meta name="csrf-token" content="synthetic"><button id="menuButton">Menu</button><aside id="sidebar"><button id="sidebarClose">Close</button><nav class="sidebar-nav">${urls.map((url,i)=>`<a href="${url}">${i}</a>`).join('')}</nav></aside><button id="sidebarBackdrop" hidden></button><main class="main-panel"></main>${html}`,{runScripts:'outside-only'});
 const w=dom.window,d=w.document;
 w.matchMedia=()=>({matches:mobile,addEventListener(){}});w.HTMLElement.prototype.scrollIntoView=function(){};
 const dialog=d.querySelector('dialog');dialog.showModal=function(){this.open=true};dialog.close=function(){this.open=false};
 let conflict=false,readFailure=false,forcedResponse=null,networkFailure=false;
 let server={eligible:true,started:false,step:0,version:0},requests=[],failNext=false;
 w.fetch=async(url,options)=>{if(networkFailure)throw new w.TypeError('synthetic secret must not display');if(forcedResponse)return forcedResponse;if(options.method==='GET'){check(options.cache==='no-store');return readFailure?{ok:false,status:500}:{ok:true,json:async()=>server};}if(conflict){conflict=false;return {ok:false,status:409};}const input=JSON.parse(options.body);requests.push(input);if(failNext){failNext=false;return {ok:false};}check(options.headers['X-CSRFToken']==='synthetic');server={...server,started:true,step:input.step,version:server.version+1,eligible:!['skip','finish'].includes(input.action)};return {ok:true,json:async()=>server};};
 w.eval(fs.readFileSync('app/static/js/app.js','utf8'));w.eval(fs.readFileSync('app/static/js/tour.js','utf8'));
 const click=async selector=>{d.querySelector(selector).click();await new Promise(r=>setTimeout(r,0));};
 check(dialog.open);check(d.querySelector('[data-tour-back]').hidden);check(d.querySelector('#tour-title').textContent==='Welcome to StockBridge!');
 await click('[data-tour-start]');check(d.querySelector('#tour-counter').textContent==='Step 1 of 7');
 check(d.querySelectorAll('.tour-highlight').length===1);check(d.querySelector('.tour-highlight').getAttribute('href')===urls[0]);
 if(mobile){check(d.querySelector('#sidebar').classList.contains('open'));check(d.querySelector('.main-panel').inert);}
 // Authentication, CSRF, server, redirected login and network failures stay open.
 for(const [response,message] of [[{ok:false,status:401},'session may have expired'],[{ok:false,status:403},'not accepted'],[{ok:false,status:400},'not accepted'],[{ok:false,status:500},'server could not save'],[{ok:true,status:200,redirected:true},'session may have expired']]){
  forcedResponse=response;await click('[data-tour-next]');check(dialog.open);check(d.querySelector('#tour-error').textContent.includes(message));check(d.querySelector('#tour-counter').textContent==='Step 1 of 7');
 }
 forcedResponse=null;networkFailure=true;await click('[data-tour-next]');check(dialog.open);check(d.querySelector('#tour-error').textContent.includes('connection'));check(!d.querySelector('#tour-error').textContent.includes('synthetic secret'));networkFailure=false;
 // Another device advances: synchronize without replaying the rejected write.
 server={...server,step:1,version:server.version+1};conflict=true;
 const before=requests.length;await click('[data-tour-next]');
 check(requests.length===before);check(d.querySelector('#tour-title').textContent==='Products');
 check(d.querySelector('#tour-error').textContent.includes('updated elsewhere'));
 await click('[data-tour-back]');
 // Retrieval failure must leave the popup open and retain the confirmed step.
 conflict=true;readFailure=true;await click('[data-tour-next]');
 check(dialog.open);check(d.querySelector('#tour-title').textContent==='Dashboard');
 check(d.querySelector('#tour-error').textContent.includes('Could not retrieve'));
 readFailure=false;
 failNext=true;await click('[data-tour-next]');check(d.querySelector('#tour-counter').textContent==='Step 1 of 7');check(d.querySelector('#tour-error').textContent.includes('server could not save'));check(!d.querySelector('[data-tour-next]').disabled);
 await click('[data-tour-next]');check(d.querySelector('#tour-title').textContent==='Products');await click('[data-tour-back]');check(d.querySelector('#tour-title').textContent==='Dashboard');
 if(terminal==='remote-finish'||terminal==='remote-skip'){
  server={eligible:false,status:terminal==='remote-finish'?'CUSTOMER_TOUR_FINISHED':'CUSTOMER_TOUR_SKIPPED',version:99};conflict=true;
  await click('[data-tour-next]');
 }else if(terminal==='finish'){
  for(let i=1;i<7;i++){await click('[data-tour-next]');check(d.querySelector('.tour-highlight').getAttribute('href')===urls[i]);}
  check(d.querySelector('[data-tour-next]').hidden);check(!d.querySelector('[data-tour-finish]').hidden);await click('[data-tour-finish]');
 }else if(terminal==='skip'){await click('[data-tour-skip]');}
 else{dialog.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));check(server.eligible);check(!requests.some(r=>['skip','finish'].includes(r.action)));}
 check(!dialog.open);check(d.querySelectorAll('.tour-highlight').length===0);check(!d.querySelector('#sidebar').classList.contains('open'));check(d.activeElement===d.querySelector(mobile?'#menuButton':'.sidebar-nav a'));
 check(!html.includes('Restart Tour')&&!html.includes('Take Tour Again')&&!html.includes('Replay Tour'));
 dom.window.close();
}
(async()=>{for(const mobile of [false,true])for(const terminal of ['finish','skip','interrupt','remote-finish','remote-skip'])await scenario(mobile,terminal);console.log(`PASS: ${count} guided-tour DOM assertions`);})().catch(e=>{console.error(e);process.exitCode=1});
