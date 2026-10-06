// Presentation assertions; not a visual browser test.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function el(dataset={}) { return {dataset,attrs:{},handlers:{},textContent:'',
  addEventListener(event,fn) {this.handlers[event]=fn;},
  setAttribute(name,value) {this.attrs[name]=value;}}; }
const monthly=el({priceInterval:'monthly'}),yearly=el({priceInterval:'yearly'});
const basic=el({priceMonthly:'₦3,000/month',priceYearly:'₦30,000/year'});
const plus=el({priceMonthly:'₦5,000/month',priceYearly:'₦50,000/year'});
let focused=false;const link=el();const summary={focus(){focused=true;}};
const menu={open:true,querySelectorAll(){return [link];},querySelector(){return summary;}};
const handlers={};const document={querySelectorAll(selector){return selector==='[data-price-interval]'?[monthly,yearly]:[basic,plus];},
  querySelector(){return menu;},addEventListener(event,fn){handlers[event]=fn;}};
vm.runInNewContext(fs.readFileSync('app/static/js/public.js','utf8'),{document});
yearly.handlers.click();assert.equal(basic.textContent,'₦30,000/year');assert.equal(plus.textContent,'₦50,000/year');
assert.equal(yearly.attrs['aria-pressed'],'true');assert.equal(monthly.attrs['aria-pressed'],'false');
monthly.handlers.click();assert.equal(basic.textContent,'₦3,000/month');assert.equal(plus.textContent,'₦5,000/month');
assert.equal(monthly.attrs['aria-pressed'],'true');assert.equal(yearly.attrs['aria-pressed'],'false');
link.handlers.click();assert.equal(menu.open,false);
menu.open=true;handlers.keydown({key:'Escape'});assert.equal(menu.open,false);assert.equal(focused,true);
console.log('PASS: public monthly/yearly prices, pressed state, mobile links and Escape focus (11 assertions).');
