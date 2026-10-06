// Lifecycle coverage for public presentation; no browser viewport PASS implied.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
let assertions=0;
const equal=(a,b)=>{assert.equal(a,b);assertions++;};
const element=()=>({hidden:false,disabled:false,textContent:'',dataset:{},attrs:{},handlers:{},classes:new Set(),
 setAttribute(k,v){this.attrs[k]=v;},contains(){return false;},matches(){return true;},
 addEventListener(k,fn){const old=this.handlers[k];this.handlers[k]=old?(e=>{old(e);fn(e);}):fn;},
 classList:{add(){}}});
function mount({reduced=false,observer=true,auth=true,profit=true,journey=true}={}){
 const steps=Array.from({length:6},element),panels=Array.from({length:6},element);
 const journeyRoot=element();journeyRoot.querySelectorAll=s=>s.includes('step')?steps:panels;
 const counts=[150000,80000,70000,25000,45000].map((value,i)=>{const e=element();e.dataset={count:String(value),prefix:[1,3].includes(i)?'− ':''};return e;});
 const rows=counts.map(e=>{const row=element();row.querySelector=()=>e;return row;});
 const skip=element(),story=element();story.querySelectorAll=()=>rows;story.querySelector=()=>skip;
 const authSteps=Array.from({length:4},element),authPanels=Array.from({length:4},element),play=element(),controls=element();
 const demo=element();demo.querySelectorAll=s=>s.includes('step')?authSteps:authPanels;demo.querySelector=s=>s.includes('controls')?controls:play;
 const media=element();media.matches=reduced;
 const timers=new Map(),frames=new Map(),observers=[];let id=0,clock=0;
 const document=element();document.hidden=false;document.querySelector=s=>s.includes('journey')?(journey?journeyRoot:null):s.includes('profit')?(profit?story:null):(auth?demo:null);
 const window={matchMedia:()=>media,setTimeout(fn,delay){timers.set(++id,{fn,delay});return id;},clearTimeout(k){timers.delete(k);},requestAnimationFrame(fn){frames.set(++id,fn);return id;},cancelAnimationFrame(k){frames.delete(k);}};
 if(observer)window.IntersectionObserver=class{constructor(fn){this.fn=fn;this.disconnected=false;observers.push(this);}observe(){}disconnect(){this.disconnected=true;}};
 vm.runInNewContext(fs.readFileSync('app/static/js/marketing.js','utf8'),{window,document,performance:{now:()=>clock}});
 const tick=()=>{const [k,t]=[...timers][0];timers.delete(k);t.fn();};
 const frame=()=>{clock+=225;const [k,fn]=[...frames][0];frames.delete(k);fn(clock);};
 return {steps,panels,counts,rows,skip,story,authSteps,authPanels,play,controls,demo,media,timers,frames,observers,document,tick,frame};
}
let s=mount({profit:false});
s.steps.forEach((step,i)=>{equal(step.disabled,false);step.handlers.click();equal(s.panels.filter(p=>!p.hidden).length,1);equal(s.panels[i].hidden,false);equal(step.attrs['aria-pressed'],'true');});
equal(s.timers.size,1);equal([...s.timers.values()][0].delay,5500);
for(let i=0;i<8;i++)s.tick();equal(s.authPanels[0].hidden,false);equal(s.play.textContent,'Pause preview');
s.authSteps[3].handlers.click();equal([...s.timers.values()][0].delay,7000);s.tick();equal(s.authPanels[0].hidden,false);
s.document.hidden=true;s.document.handlers.visibilitychange();equal(s.timers.size,0);
s.document.hidden=false;s.document.handlers.visibilitychange();equal(s.timers.size,1);
s.observers[0].fn([{isIntersecting:false}]);equal(s.timers.size,0);s.observers[0].fn([{isIntersecting:true}]);equal(s.timers.size,1);
s.demo.handlers.focusin({target:s.play});equal(s.timers.size,0);s.demo.handlers.focusout({relatedTarget:null});equal(s.timers.size,1);
s.play.handlers.click();equal(s.timers.size,0);s.play.handlers.click();equal(s.timers.size,1);
s.media.matches=true;s.media.handlers.change();equal(s.timers.size,0);equal(s.play.disabled,true);
s=mount({auth:false});equal(s.timers.size,0);s.observers[0].fn([{isIntersecting:true}]);equal(s.rows[0].attrs['data-revealed'],'true');equal(s.rows[1].attrs['data-revealed'],undefined);
s.frame();equal(s.counts[0].textContent,'₦131,250');s.frame();equal(s.counts[0].textContent,'₦150,000');
s.tick();equal(s.rows[1].attrs['data-revealed'],'true');s.frame();s.frame();equal(s.counts[1].textContent,'− ₦80,000');
s.skip.handlers.click();equal(s.timers.size,0);equal(s.frames.size,0);equal(s.counts[4].textContent,'₦45,000');equal(s.skip.disabled,true);equal(s.observers[0].disconnected,true);
s=mount({auth:false});s.observers[0].fn([{isIntersecting:true}]);s.document.hidden=true;s.document.handlers.visibilitychange();equal(s.timers.size,0);equal(s.frames.size,0);equal(s.rows.every(r=>r.attrs['data-revealed']==='true'),true);
s=mount({auth:false});s.observers[0].fn([{isIntersecting:true}]);s.media.matches=true;s.media.handlers.change();equal(s.frames.size,0);equal(s.counts[2].textContent,'₦70,000');
s=mount({reduced:true});equal(s.timers.size,0);equal(s.frames.size,0);equal(s.play.disabled,true);s.authSteps[2].handlers.click();equal(s.authPanels[2].hidden,false);equal(s.timers.size,0);
s=mount({observer:false,auth:false});equal(s.timers.size,0);equal(s.frames.size,0);
console.log(`PASS: manual journey, one-time profit arithmetic/count-up, skip/hidden-tab cleanup, auth loop/pause/focus/reduced motion (${assertions} assertions).`);
