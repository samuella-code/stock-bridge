// Motion lifecycle tests; these do not claim browser or responsive visual PASS.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let assertions = 0;
function equal(a,b) {assert.equal(a,b); assertions++;}
function el(text='') {
  const classes = new Set();
  return {textContent:text,hidden:false,disabled:false,attrs:{},handlers:{},dataset:{},
    classList:{add(...items){items.forEach(i=>classes.add(i));},remove(i){classes.delete(i);},contains(i){return classes.has(i);}},
    matches(){return true;},contains(){return false;},setAttribute(k,v){this.attrs[k]=v;},addEventListener(k,fn){const previous=this.handlers[k];this.handlers[k]=previous?(event=>{previous(event);fn(event);}):fn;}};
}
function mount({reduced=false,compact=false,observer=true}={}) {
  const names=['Dashboard','Products','Record Sale','Stock updates','Expenses','Reports'];
  const panels=names.map((name,i)=>{const p=el();p.dataset={sales:i<3?'₦0':'₦5,000',profit:i<3?'₦0':i===3?'₦2,000':'₦1,500',stock:i<3?'100':'90'};p.querySelector=()=>el(name);return p;});
  const steps=names.map(el),play=el(),caption=el(),announce=el(),controls=el();controls.hidden=true;
  const metrics={sales:el(),profit:el(),stock:el()},sections=[el(),el()],observers=[];
  const root=el();root.querySelectorAll=s=>s==='[data-showcase-panel]'?panels:s==='[data-showcase-side]'?[]:steps;
  root.querySelector=s=>({'[data-showcase-play]':play,'[data-showcase-caption]':caption,'[data-showcase-announcement]':announce,'[data-showcase-controls]':controls,
    '[data-showcase-sales]':metrics.sales,'[data-showcase-profit]':metrics.profit,'[data-showcase-stock]':metrics.stock})[s];
  const media=[el(),el()];media[0].matches=reduced;media[1].matches=compact;
  const timers=new Map();let id=0;
  const document={hidden:false,handlers:{},querySelector:()=>root,querySelectorAll:()=>sections,addEventListener(k,fn){const previous=this.handlers[k];this.handlers[k]=previous?(event=>{previous(event);fn(event);}):fn;}};
  const window={matchMedia:q=>media[q.includes('reduced')?0:1],setTimeout(fn,delay){timers.set(++id,{fn,delay});return id;},clearTimeout(id){timers.delete(id);}};
  if(observer)window.IntersectionObserver=class {constructor(fn){this.fn=fn;this.observed=[];observers.push(this);}observe(e){this.observed.push(e);}unobserve(e){this.observed=this.observed.filter(i=>i!==e);}disconnect(){this.observed=[];}};
  vm.runInNewContext(fs.readFileSync('app/static/js/showcase.js','utf8'),{document,window});
  const tick=()=>{const [key,timer]=[...timers.entries()][0];timers.delete(key);timer.fn();};
  return {panels,steps,play,caption,announce,controls,metrics,sections,root,media,timers,observers,document,tick};
}
let s=mount();
equal(s.controls.hidden,false);equal(s.timers.size,1);equal(s.panels.filter(p=>!p.hidden).length,1);
s.tick();equal(s.panels[1].hidden,false);equal(s.steps[1].attrs['aria-pressed'],'true');equal(s.announce.textContent,'');
s.document.hidden=true;s.document.handlers.visibilitychange();equal(s.timers.size,0);
s.document.hidden=false;s.document.handlers.visibilitychange();equal(s.timers.size,1);
s.observers[0].fn([{isIntersecting:false}]);equal(s.timers.size,0);
s.observers[0].fn([{isIntersecting:true}]);equal(s.timers.size,1);
s.root.handlers.pointerenter({pointerType:'mouse'});equal(s.timers.size,0);
s.root.handlers.pointerleave({pointerType:'mouse'});equal(s.timers.size,1);
s.play.handlers.click();equal(s.timers.size,0);equal(s.play.textContent,'Play showcase');
s.play.handlers.click();equal(s.timers.size,1);
s.root.handlers.focusin({target:s.steps[0]});equal(s.timers.size,0);
s.steps[3].handlers.click();equal(s.metrics.stock.textContent,'90');equal(s.metrics.sales.textContent,'₦5,000');equal(s.announce.textContent,'Step 4 of 6: Stock updates');
s.root.handlers.focusout({relatedTarget:null});equal(s.timers.size,1);equal([...s.timers.values()][0].delay,7000);
s.tick();equal(s.panels[4].hidden,false);equal([...s.timers.values()][0].delay,4500);
s.steps[5].handlers.click();equal(s.metrics.profit.textContent,'₦1,500');equal(s.play.textContent,'Pause showcase');
s.tick();equal(s.panels[0].hidden,false);equal(s.metrics.stock.textContent,'100');equal(s.timers.size,1);
for(let i=0;i<18;i++)s.tick();equal(s.panels[0].hidden,false);equal(s.timers.size,1);equal(s.play.textContent,'Pause showcase');
s.play.handlers.click();equal(s.timers.size,0);s.steps[5].handlers.click();equal(s.timers.size,0);equal(s.play.textContent,'Play showcase');
s.play.handlers.click();s.tick();equal(s.panels[0].hidden,false);equal(s.timers.size,1);
s.media[0].matches=true;s.media[0].handlers.change();equal(s.timers.size,0);equal(s.play.disabled,true);
equal(s.sections.every(e=>!e.classList.contains('reveal-pending')),true);
s=mount({reduced:true});equal(s.timers.size,0);equal(s.play.disabled,true);equal(s.sections[0].classList.contains('reveal-pending'),false);
s.steps[4].handlers.click();equal(s.panels[4].hidden,false);equal(s.metrics.profit.textContent,'₦1,500');equal(s.timers.size,0);
s=mount({compact:true});equal(s.timers.size,1);equal(s.play.disabled,false);
s.root.handlers.pointerenter({pointerType:'mouse'});equal(s.timers.size,1);
s.root.handlers.focusin({target:{matches:()=>false}});equal(s.timers.size,1);
s.steps[2].handlers.click();equal(s.panels[2].hidden,false);equal([...s.timers.values()][0].delay,7000);
s.tick();equal(s.panels[3].hidden,false);equal(s.timers.size,1);
for(let i=0;i<9;i++)s.tick();equal(s.panels[0].hidden,false);equal(s.timers.size,1);
s.media[0].matches=true;s.media[0].handlers.change();equal(s.timers.size,0);
s.media[0].matches=false;s.media[0].handlers.change();equal(s.timers.size,1);
s.media[1].matches=false;s.media[1].handlers.change();equal(s.timers.size,1);

s=mount();equal(s.sections[0].classList.contains('reveal-pending'),true);
s.observers[1].fn([{isIntersecting:true,target:s.sections[0]}]);equal(s.sections[0].classList.contains('reveal-pending'),false);
s.sections[1].handlers.focusin();equal(s.sections[1].classList.contains('reveal-pending'),false);
s=mount({observer:false});equal(s.sections[0].classList.contains('reveal-pending'),false);equal(s.timers.size,1);
console.log(`PASS: continuous loops, 4.5s interval/7s manual delay, pause/play, hidden tab/offscreen, keyboard focus, mobile autoplay, reduced motion and progressive reveals (${assertions} assertions).`);
