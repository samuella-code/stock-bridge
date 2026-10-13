const assert=require('node:assert/strict'),fs=require('node:fs'),{JSDOM}=require('jsdom');
let count=0;function check(value){assert.ok(value);count++;}
(async()=>{
 const dom=new JSDOM(`<form><input name="submission_token" value="original"><div class="basket" data-mode="restock" data-lookup="/products/lookup"><input class="basket-search"><details class="basket-browse"><summary>Browse Products</summary><button type="button" class="restock-product-picker">Refresh</button><div class="basket-browse-results"></div></details><div class="basket-results"></div><p class="basket-message"></p><p class="basket-empty">Empty</p><p class="basket-count"></p><div class="basket-lines"></div><strong class="basket-total"></strong><p class="restock-review"></p></div><button type="submit">Complete</button></form>`,{runScripts:'outside-only'});
 const w=dom.window,d=w.document;let requests=0;
 w.fetch=async()=>{requests++;return {ok:true,json:async()=>({products:[{id:1,name:'Rice',stock:5,unit:'bag',cost:'100',price:'150'},{id:2,name:'Water',stock:2,unit:'bottle',cost:'300',price:'400'}]})};};
 w.eval(fs.readFileSync('app/static/js/basket.js','utf8'));
 const browse=d.querySelector('details'),results=d.querySelector('.basket-browse-results');
 check(!browse.open);check(requests===0);check(!d.querySelector('.basket-empty').hidden);
 browse.open=true;await new Promise(r=>setTimeout(r,5));check(requests===1);check(results.children.length===2);
 results.children[0].click();check(d.querySelector('.expected-stock').textContent.includes('6'));
 results.children[1].click();check(d.querySelector('.basket-count').textContent==='2 products selected');
 const quantities=d.querySelectorAll('[name=quantity]');quantities[0].value='3';quantities[1].value='2';quantities[0].dispatchEvent(new w.Event('input',{bubbles:true}));
 check(d.querySelector('.basket-total').textContent.includes('900.00'));check(d.querySelector('.restock-review').textContent.includes('5 units'));check(d.querySelector('.expected-stock').textContent.includes('8'));
 const submit=d.querySelector('button[type=submit]');quantities[0].value='0';quantities[0].dispatchEvent(new w.Event('input',{bubbles:true}));check(submit.disabled);check(!d.querySelector('.stock-warning').hidden);
 check(!d.querySelector('form').dispatchEvent(new w.Event('submit',{cancelable:true})));w.dispatchEvent(new w.Event('pageshow'));check(submit.disabled);
 quantities[0].value='3';const cost=d.querySelector('.line-price');cost.value='-1';cost.dispatchEvent(new w.Event('input',{bubbles:true}));check(submit.disabled);check(d.querySelector('.stock-warning').textContent.includes('purchase cost'));
 cost.value='100';cost.dispatchEvent(new w.Event('input',{bubbles:true}));check(!submit.disabled);check(d.querySelector('[name=submission_token]').value==='original');
 d.querySelector('form').dispatchEvent(new w.Event('submit',{cancelable:true}));check(submit.disabled);cost.dispatchEvent(new w.Event('input',{bubbles:true}));check(submit.disabled);w.dispatchEvent(new w.Event('pageshow'));check(!submit.disabled);
 browse.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));check(!browse.open);check(d.activeElement===d.querySelector('.basket-search'));
 dom.window.close();console.log(`PASS: ${count} Restocking UX assertions`);
})().catch(e=>{console.error(e);process.exitCode=1;});
