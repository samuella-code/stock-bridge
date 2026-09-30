// NODE_PATH=/tmp/stockbridge-dom-tests/node_modules node tests/mobile_ui.cjs /tmp/stockbridge-dom-html
// Test-only dependencies: jsdom@26.1.0 and bwip-js@4.7.0.
const {JSDOM}=require('jsdom'),bwip=require('bwip-js'),fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),source=fs.readFileSync(path.join(root,'app/static/js/barcode.js'),'utf8');
const tick=()=>new Promise(r=>setTimeout(r,0));
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
function scanner(){
 const dom=new JSDOM('<button data-scan-target="#code">Scan</button><input id="code">',{url:'https://stockbridge.example/products/new',runScripts:'outside-only'}),w=dom.window;
 w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new w.Event('close'));};
 w.HTMLMediaElement.prototype.play=async function(){};Object.defineProperty(w.HTMLMediaElement.prototype,'readyState',{get:()=>2});
 Object.defineProperty(w,'isSecureContext',{value:true});
 const track={stopped:0,stop(){this.stopped++;}},stream={getTracks:()=>[track]};
 Object.defineProperty(w.navigator,'mediaDevices',{value:{getUserMedia:async constraints=>{assert.equal(constraints.video.facingMode.ideal,'environment');assert.equal(constraints.audio,false);return stream;}}});
 w.eval(source);return {dom,w,d:w.document,track,stream};
}
function fallback(env,returning){
 const state={calls:0,stops:0};
 env.w.ZXingBrowser={BrowserMultiFormatOneDReader:class{
  async decodeFromStream(stream,video,callback){state.calls++;state.callback=callback;state.controls={stop(){state.stops++;}};return returning?returning.promise:state.controls;}
  async decodeFromImageUrl(url){state.photo=url;return {getText:()=> '000555'};}
 }};return state;
}
function cleanup(env){env.dom.window.close();}
(async()=>{
 // Decode real generated retail barcode pixels with the exact bundled UMD asset.
 const pixels=new JSDOM('',{runScripts:'outside-only'});pixels.window.eval(fs.readFileSync(path.join(root,'app/static/js/vendor/zxing-browser-0.2.1.min.js'),'utf8'));
 for(const [bcid,text] of [['ean13','5901234123457'],['ean8','96385074'],['upca','012345678905'],['code128','0012345678'],['code39','SB-12345'],['interleaved2of5','00123456']]){
  const runs=bwip.raw({bcid,text,includecheck:false})[0].sbs,scale=3,margin=45,width=runs.reduce((a,b)=>a+b,0)*scale+margin*2,height=120,data=new Uint8ClampedArray(width*height*4);data.fill(255);let x=margin;
  runs.forEach((run,i)=>{const end=x+run*scale;if(i%2===0)for(let y=0;y<height;y++)for(let col=x;col<end;col++){const p=(y*width+col)*4;data[p]=data[p+1]=data[p+2]=0;}x=end;});
  const canvas=pixels.window.document.createElement('canvas');canvas.width=width;canvas.height=height;canvas.getContext=()=>({getImageData:()=>({data})});
  const result=new pixels.window.ZXingBrowser.BrowserMultiFormatOneDReader().decodeFromCanvas(canvas);assert.equal(result.getText(),text,bcid);
 }pixels.window.close();
 // Browsers without BarcodeDetector still start their rear camera and decode once.
 let env=scanner(),state=fallback(env);await env.w.StockBridgeScan(code=>env.d.querySelector('#code').value=code);assert.equal(state.calls,1);assert.equal(env.d.querySelector('video').hidden,false);assert.notEqual(env.d.activeElement,env.d.querySelector('.manual-barcode'));
 state.callback(undefined,Error('No barcode yet'),state.controls);assert.equal(env.track.stopped,0);
 state.callback({getText:()=> '001234'},undefined,state.controls);assert.equal(env.d.querySelector('#code').value,'001234');assert.ok(env.track.stopped);assert.equal(env.d.querySelector('dialog').open,false);assert.equal(env.d.querySelector('video').srcObject,null);cleanup(env);
 // Native scanning remains available. Failed/empty native support uses fallback.
 for(const mode of ['native','detect-error','formats-error','empty']){
  env=scanner();state=fallback(env);env.w.BarcodeDetector=class{static async getSupportedFormats(){if(mode==='formats-error')throw Error();return mode==='empty'?[]:['ean_13'];}async detect(){if(mode==='detect-error')throw Error();return [{rawValue:'5901234123457'}];}};
  await env.w.StockBridgeScan(code=>env.d.querySelector('#code').value=code);await tick();await tick();
  if(mode==='native'){assert.equal(state.calls,0);assert.equal(env.d.querySelector('#code').value,'5901234123457');}else assert.equal(state.calls,1,mode);
  env.d.querySelector('.scanner-close').click();assert.ok(env.track.stopped);cleanup(env);
 }
 // Every dismissal releases the camera and decoding loop.
 for(const method of ['close','cancel','pagehide','manual']){
  env=scanner();state=fallback(env);await env.w.StockBridgeScan(()=>{});
  if(method==='manual'){env.d.querySelector('.manual-barcode').value='0123';env.d.querySelector('.scanner-use').click();}
  else if(method==='close')env.d.querySelector('.scanner-close').click();
  else (method==='pagehide'?env.w:env.d.querySelector('dialog')).dispatchEvent(new env.w.Event(method));
  assert.ok(env.track.stopped,method);assert.ok(state.stops,method);assert.equal(env.d.querySelector('video').hidden,true);cleanup(env);
 }
 // Permission denied: camera retry and manual entry remain usable.
 env=scanner();env.w.navigator.mediaDevices.getUserMedia=async()=>{throw Error('NotAllowedError');};await env.w.StockBridgeScan(code=>env.d.querySelector('#code').value=code);assert.match(env.d.querySelector('.scanner-message').textContent,/permission denied/);assert.equal(env.d.querySelector('.scanner-retry').hidden,false);env.d.querySelector('.manual-barcode').value='0011';env.d.querySelector('.scanner-use').click();assert.equal(env.d.querySelector('#code').value,'0011');cleanup(env);
 // Cancel while permission is pending must stop the later stream.
 env=scanner();const permission=deferred();env.w.navigator.mediaDevices.getUserMedia=()=>permission.promise;const opening=env.w.StockBridgeScan(()=>assert.fail('stale callback'));env.d.querySelector('.scanner-close').click();permission.resolve(env.stream);await opening;assert.ok(env.track.stopped);cleanup(env);
 // Cancel while decoder initialization is pending must stop its eventual controls.
 env=scanner();const initializing=deferred();state=fallback(env,initializing);const starting=env.w.StockBridgeScan(()=>assert.fail('stale decoder'));await tick();env.d.querySelector('.scanner-close').click();initializing.resolve(state.controls);await starting;assert.ok(state.stops);assert.ok(env.track.stopped);cleanup(env);
 // Decoder is lazy, same-origin, and a failed load can be retried.
 env=scanner();let attempts=env.w.StockBridgeScan(()=>{});await tick();let script=env.d.querySelector('script');assert.equal(script.src,'https://stockbridge.example/static/js/vendor/zxing-browser-0.2.1.min.js');script.dispatchEvent(new env.w.Event('error'));await attempts;assert.match(env.d.querySelector('.scanner-message').textContent,/could not start/);
 attempts=env.w.StockBridgeScan(()=>{});await tick();state=fallback(env);script=env.d.querySelector('script');script.dispatchEvent(new env.w.Event('load'));await attempts;assert.equal(state.calls,1);env.d.querySelector('.scanner-close').click();cleanup(env);
 // Decode selected photos locally and revoke their temporary object URL.
 env=scanner();state=fallback(env);let revoked=0;env.w.URL.createObjectURL=()=> 'blob:test-photo';env.w.URL.revokeObjectURL=()=>revoked++;await env.w.StockBridgeScan(code=>env.d.querySelector('#code').value=code);const photo=env.d.querySelector('.scanner-photo');Object.defineProperty(photo,'files',{value:[new env.w.File(['x'],'barcode.jpg',{type:'image/jpeg'})]});photo.dispatchEvent(new env.w.Event('change'));await tick();await tick();assert.equal(state.photo,'blob:test-photo');assert.equal(revoked,1);assert.equal(env.d.querySelector('#code').value,'000555');assert.ok(env.track.stopped);cleanup(env);
 // Mobile menu behavior against the actual Flask-rendered business page.
 const dom=new JSDOM(fs.readFileSync(path.join(process.argv[2],'products.html'),'utf8'),{url:'https://stockbridge.example/products/',runScripts:'outside-only'}),w=dom.window,d=w.document,media={matches:true,addEventListener(event,cb){this.change=cb;}};w.matchMedia=()=>media;w.eval(fs.readFileSync(path.join(root,'app/static/js/app.js'),'utf8'));
 const menu=d.querySelector('#menuButton'),sidebar=d.querySelector('#sidebar'),close=d.querySelector('#sidebarClose'),backdrop=d.querySelector('#sidebarBackdrop'),main=d.querySelector('.main-panel');assert.equal(sidebar.inert,true);
 for(const exit of ['button','backdrop','escape','link']){
  menu.click();assert.ok(sidebar.classList.contains('open'));assert.equal(backdrop.hidden,false);assert.equal(main.inert,true);assert.equal(menu.getAttribute('aria-expanded'),'true');assert.equal(d.activeElement,close);
  const last=sidebar.querySelector('.logout-btn');last.focus();d.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Tab',bubbles:true,cancelable:true}));assert.equal(d.activeElement,close);d.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Tab',shiftKey:true,bubbles:true,cancelable:true}));assert.equal(d.activeElement,last);
  if(exit==='button')close.click();if(exit==='backdrop')backdrop.click();if(exit==='escape')d.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape'}));if(exit==='link')sidebar.querySelector('a').dispatchEvent(new w.Event('click'));
  assert.equal(sidebar.classList.contains('open'),false);assert.equal(backdrop.hidden,true);assert.equal(main.inert,false);assert.equal(menu.getAttribute('aria-expanded'),'false');if(exit!=='link')assert.equal(d.activeElement,menu);
 }menu.click();media.matches=false;media.change();assert.equal(sidebar.inert,false);assert.equal(sidebar.classList.contains('open'),false);assert.equal(main.inert,false);w.close();
 console.log('PASS: six real barcode formats; native/fallback camera selection; permission, cancellation, retry and cleanup; local photo decoding; mobile menu dismissal, focus trap and desktop transition.');
})().catch(error=>{console.error(error);process.exitCode=1;});
