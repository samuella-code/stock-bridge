const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
let assertions = 0;
for (const mode of ['sale','restock']) {
 for (const disabled of [false,true]) {
  const dom = new JSDOM(`<form><input name="submission_token" value="original"><div class="basket" data-mode="${mode}"><input class="basket-search"><div class="basket-results"></div><p class="basket-message"></p><div class="basket-lines"></div><strong class="basket-total"></strong></div><button type="submit" ${disabled?'disabled':''}>Complete</button></form>`,{runScripts:'outside-only'});
  dom.window.eval(fs.readFileSync('app/static/js/basket.js','utf8'));
  const button = dom.window.document.querySelector('button');
  button.disabled = true;
  dom.window.dispatchEvent(new dom.window.Event('pageshow'));
  assert.equal(button.disabled, disabled); assertions++;
  dom.window.dispatchEvent(new dom.window.Event('pageshow'));
  assert.equal(button.disabled, disabled); assertions++;
  assert.equal(dom.window.document.querySelector('[name=submission_token]').value,'original'); assertions++;
  const form = dom.window.document.querySelector('form');
  assert.equal(form.dispatchEvent(new dom.window.Event('submit',{cancelable:true})),false); assertions++;
  dom.window.close();
 }
}
console.log(`PASS: ${assertions} basket recovery, server-disabled state, token and empty-basket assertions`);
