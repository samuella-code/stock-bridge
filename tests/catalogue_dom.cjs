// npm install --prefix /tmp/stockbridge-dom-tests jsdom@26.1.0
// NODE_PATH=/tmp/stockbridge-dom-tests/node_modules node tests/catalogue_dom.cjs /tmp/stockbridge-dom-html
const {JSDOM}=require('jsdom');const fs=require('node:fs');const assert=require('node:assert/strict');const path=require('node:path');
const root=path.resolve(__dirname,'..'),html=process.argv[2];const pause=()=>new Promise(r=>setTimeout(r,280));
function page(name,scripts){const dom=new JSDOM(fs.readFileSync(path.join(html,name+'.html'),'utf8'),{url:'http://localhost',runScripts:'outside-only'});scripts.forEach(s=>dom.window.eval(fs.readFileSync(path.join(root,'app/static/js',s),'utf8')));return dom;}
(async()=>{
 let dom=page('quick',['quick-add.js']),d=dom.window.document;
 d.querySelector('[name=category]').value='Drinks';d.querySelector('[name=unit]').value='bottle';d.querySelector('[name=name]').value='Coke';d.querySelector('#add-quick').click();
 assert.equal(d.querySelectorAll('.quick-row').length,2);assert.equal(d.querySelectorAll('[name=category]')[1].value,'Drinks');assert.equal(d.querySelectorAll('[name=name]')[1].value,'');d.querySelectorAll('.remove-quick')[1].click();assert.equal(d.querySelectorAll('.quick-row').length,1);dom.window.close();
 for(const mode of ['sale','restock']){
  dom=page(mode,[]);d=dom.window.document;const urls=[];
  dom.window.fetch=async url=>{urls.push(url);return {ok:true,json:async()=>({products:url.includes('unknown')?[]:[{id:1,name:'Coca-Cola',stock:20,unit:'bottle',price:'350',cost:'250'}]})};};
  dom.window.eval(fs.readFileSync(path.join(root,'app/static/js/basket.js'),'utf8'));
  const input=d.querySelector('.basket-search');input.value='coke';input.dispatchEvent(new dom.window.Event('input'));await pause();d.querySelector('.search-result').click();assert.equal(d.querySelector('[name=product_id]').value,'1');
  d.querySelector('[name=quantity]').value='3';d.querySelector('[name=quantity]').dispatchEvent(new dom.window.Event('input',{bubbles:true}));assert.match(d.querySelector('.basket-total').textContent,mode==='sale'?/1,050\.00/:/750\.00/);
  input.value='coke';input.dispatchEvent(new dom.window.Event('input'));await pause();d.querySelector('.search-result').click();assert.equal(d.querySelectorAll('.basket-line').length,1);assert.match(d.querySelector('.basket-message').textContent,/already in the basket/);assert.ok(urls.every(u=>u.includes('?q=')));
  d.querySelector('.basket-line button').click();assert.equal(d.querySelectorAll('.basket-line').length,0);const form=d.querySelector('.basket').closest('form');assert.equal(form.dispatchEvent(new dom.window.Event('submit',{cancelable:true})),false);dom.window.close();
 }
 dom=page('preview',['import-preview.js']);d=dom.window.document;assert.equal([...d.querySelectorAll('.preview-row')].filter(r=>!r.hidden).length,50);d.querySelector('[name="rows.0.name"]').value='Edited on phone';d.querySelector('[name="rows.0.action"]').value='skip';d.querySelector('#preview-next').click();assert.equal([...d.querySelectorAll('.preview-row')].filter(r=>!r.hidden).length,1);d.querySelector('#preview-prev').click();assert.equal([...d.querySelectorAll('.preview-row')].filter(r=>!r.hidden).length,50);assert.equal(d.querySelector('[name="rows.0.name"]').value,'Edited on phone');assert.equal(d.querySelector('[name="rows.0.action"]').value,'skip');dom.window.close();
 dom=page('import',[]);d=dom.window.document;assert.equal(d.querySelectorAll('input[name=method]').length,3);assert.ok(d.querySelector('textarea[name=product_list]'));assert.ok(d.querySelector('input[name=text_file]'));dom.window.close();
 dom=page('products',['app.js']);d=dom.window.document;assert.ok(d.querySelector('table td[data-label="Current Stock"]'));assert.ok(d.querySelector('form[method=POST] input[name=csrf_token]'));dom.window.close();
 console.log('PASS: Quick Add, sale/restock search baskets, totals, duplicate selection, preview paging and labelled mobile cards.');
})().catch(e=>{console.error(e);process.exitCode=1;});
