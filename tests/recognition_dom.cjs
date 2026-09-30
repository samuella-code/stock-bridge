// NODE_PATH=... node tests/recognition_dom.cjs /path/to/rendered-fixtures
const {JSDOM}=require('jsdom'),fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'../app/static/js/barcode.js'),'utf8');
const reference={barcode:'5449000000996',product_name:'Coca-Cola',brand:'Coca-Cola',variant:'330 ml',category:'Drinks',description:'Brand: Coca-Cola; Pack size: 330 ml',provider:'Open Food Facts',source_url:'https://world.openfoodfacts.org/product/5449000000996',license_url:'https://opendatacommons.org/licenses/odbl/1-0/'};
const tick=()=>new Promise(r=>setTimeout(r,0));
function page(name){const dom=new JSDOM(fs.readFileSync(path.join(process.argv[2],name+'.html'),'utf8'),{url:'https://stockbridge.example/',runScripts:'outside-only'}),w=dom.window;Object.defineProperty(w,'isSecureContext',{value:false});w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new w.Event('close'));};w.eval(source);return {dom,w,d:w.document};}
(async()=>{
 let env=page('product-form');const form=env.d.querySelector('.product-form'),barcode=form.querySelector('[name=barcode]');barcode.value=reference.barcode;
 const original={};for(const field of ['buying_price','selling_price','stock_quantity','minimum_stock_level','unit'])original[field]=form.querySelector(`[name=${field}]`).value;
 form.querySelector('[name=category]').value='My own category';env.w.fetch=async()=>({ok:true,json:async()=>({status:'recognized',products:[],reference})});form.querySelector('.identify-product').click();await tick();await tick();
 assert.equal(form.querySelector('[name=name]').value,'Coca-Cola');assert.equal(form.querySelector('[name=category]').value,'My own category');assert.equal(form.querySelector('[name=description]').value,reference.description);
 for(const [field,value] of Object.entries(original))assert.equal(form.querySelector(`[name=${field}]`).value,value,field);
 assert.match(env.d.querySelector('#product-recognition').textContent,/Open Database License/);
 // Never overwrite edited descriptions or names after another recognition.
 form.querySelector('[name=name]').value='My edited name';form.querySelector('[name=description]').value='My notes';form.querySelector('.identify-product').click();await tick();await tick();assert.equal(form.querySelector('[name=name]').value,'My edited name');assert.equal(form.querySelector('[name=description]').value,'My notes');
 // Render external text as text, not executable markup.
 env.w.fetch=async()=>({ok:true,json:async()=>({products:[],reference:{...reference,product_name:'<img src=x onerror=alert(1)>'}})});form.querySelector('.identify-product').click();await tick();await tick();assert.equal(env.d.querySelector('#product-recognition img'),null);
 // Ignore stale responses after barcode input changes.
 let resolve;env.w.fetch=()=>new Promise(r=>resolve=r);form.querySelector('.identify-product').click();barcode.value='changed';barcode.dispatchEvent(new env.w.Event('input'));resolve({ok:true,json:async()=>({products:[],reference})});await tick();await tick();assert.equal(form.querySelector('[name=name]').value,'My edited name');env.dom.window.close();
 for(const state of ['recognized','unknown','unavailable','existing','archived']){
  env=page('scan');env.d.querySelector('#barcode-value').value=reference.barcode;
  env.w.fetch=async url=>{assert.ok(url.startsWith('/products/recognize?'));return {ok:true,json:async()=>({status:state,products:['existing','archived'].includes(state)?[{name:'My saved product',active:state==='existing',stock:42,unit:'cans',url:'/products/9'}]:[],...(state==='recognized'?{reference}:{})})};};
  env.d.querySelector('#find-barcode').click();await tick();await tick();const result=env.d.querySelector('#barcode-result');
  if(state==='recognized'){assert.match(result.textContent,/We found this product/);assert.ok(result.querySelector('.primary-btn').href.endsWith('/products/new?barcode='+reference.barcode));}
  if(['unknown','unavailable'].includes(state)){assert.match(result.textContent,/add it manually/);assert.match(result.querySelector('a').href,/barcode=5449000000996/);}
  if(['existing','archived'].includes(state)){assert.ok(result.querySelector('a').href.endsWith('/products/9'));assert.equal(result.textContent.includes('Archived'),state==='archived');}
  env.dom.window.close();
 }
 for(const name of ['sale','restock']){
  env=page(name);let scanned;env.w.StockBridgeScan=cb=>scanned=cb;env.w.fetch=async()=>({ok:true,json:async()=>({products:[]})});env.w.eval(fs.readFileSync(path.join(__dirname,'../app/static/js/basket.js'),'utf8'));env.d.querySelector('.basket-scan').click();scanned(reference.barcode);await tick();await tick();
  assert.equal(env.d.querySelectorAll('.basket-line').length,0);assert.ok(env.d.querySelector('.basket-results a').href.endsWith('/products/new?barcode='+reference.barcode));env.dom.window.close();
 }
 console.log('PASS: recognition preview, editable descriptions, stock/price preservation, HTML escaping, stale responses, existing/archived/fallback routes, sale/restock Add Product without inventory creation.');
})().catch(e=>{console.error(e);process.exitCode=1;});
