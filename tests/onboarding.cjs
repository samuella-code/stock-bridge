// Browser preference unit tests; these do not verify responsive layouts.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/js/onboarding.js', 'utf8');
function mount(key, storage, available=true) {
  const getStarted = {handlers:{},addEventListener(t,fn){this.handlers[t]=fn;}};
  const skip = {hidden:true,handlers:{},addEventListener(t,fn){this.handlers[t]=fn;}};
  const welcome = {hidden:false,dataset:{welcomeKey:key},querySelector(s){return s.includes('complete')?getStarted:skip;}};
  const focus = {called:false,focus(){this.called=true;}};
  const document = {querySelectorAll(){return [welcome];},querySelector(){return focus;}};
  const window = {localStorage:{getItem(k){if(!available)throw Error('unavailable');return storage[k];},setItem(k,v){if(!available)throw Error('unavailable');storage[k]=v;}}};
  vm.runInNewContext(source,{document,window});
  return {welcome,getStarted,skip,focus};
}
const storage={};let ui=mount('account1:business1',storage);
assert.equal(ui.welcome.hidden,false);assert.equal(ui.skip.hidden,false);
ui.skip.handlers.click();assert.equal(ui.welcome.hidden,true);assert.equal(ui.focus.called,true);
assert.equal(storage['account1:business1'],'dismissed');
assert.equal(mount('account1:business1',storage).welcome.hidden,true);
assert.equal(mount('account1:business2',storage).welcome.hidden,false);
assert.equal(mount('account2:business1',storage).welcome.hidden,false);
ui=mount('account1:business3',storage);ui.getStarted.handlers.click();assert.equal(storage['account1:business3'],'dismissed');
ui=mount('account1:business4',storage,false);assert.equal(ui.welcome.hidden,false);ui.skip.handlers.click();assert.equal(ui.welcome.hidden,true);
assert.equal(mount('account1:business4',storage,false).welcome.hidden,false);
console.log('PASS: welcome skip, keyboard focus, persistence, account/business scoping, Get Started and unavailable storage (12 assertions).');
