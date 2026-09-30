// Optional real-photo regression: NODE_PATH=... node tests/barcode_photo.cjs /path/to/photo.jpg
// Test-only dependencies: jsdom@26.1.0, @napi-rs/canvas@0.1.80.
const {JSDOM}=require('jsdom'),{createCanvas,loadImage}=require('@napi-rs/canvas');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const tick=()=>new Promise(resolve=>setTimeout(resolve,10));
(async()=>{
 const image=await loadImage(process.argv[2]);
 // Exercise the actual file-change handler with real pixels, including rotated photos.
 // User photographs are deliberately not committed to the repository.
 for(const rotation of [0,10,90]){
  const rotated=createCanvas(rotation===90?image.height:image.width,rotation===90?image.width:image.height);
  const ctx=rotated.getContext('2d');ctx.fillStyle='white';ctx.fillRect(0,0,rotated.width,rotated.height);ctx.translate(rotated.width/2,rotated.height/2);ctx.rotate(rotation*Math.PI/180);ctx.drawImage(image,-image.width/2,-image.height/2);
  const photoImage=await loadImage(rotated.toBuffer('image/png'));
  const dom=new JSDOM('<input id="code">',{url:'https://stockbridge.example/',runScripts:'outside-only'}),w=dom.window;
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new w.Event('close'));};
  Object.defineProperty(w,'isSecureContext',{value:false});
  w.Image=class{naturalWidth=photoImage.width;naturalHeight=photoImage.height;set src(value){setTimeout(()=>this.onload(),0);}};
  const canvases=new WeakMap();
  w.HTMLCanvasElement.prototype.getContext=function(){
   let canvas=canvases.get(this);if(!canvas||canvas.width!==this.width||canvas.height!==this.height){canvas=createCanvas(this.width,this.height);canvases.set(this,canvas);}
   const context=canvas.getContext('2d');return new Proxy(context,{get(target,key){if(key==='drawImage')return (source,...args)=>target.drawImage(photoImage,...args);const value=target[key];return typeof value==='function'?value.bind(target):value;},set(target,key,value){target[key]=value;return true;}});
  };
  let revoked=0;w.URL.createObjectURL=()=> 'blob:local';w.URL.revokeObjectURL=()=>revoked++;
  w.eval(fs.readFileSync(path.join(__dirname,'../app/static/js/vendor/zxing-browser-0.2.1.min.js'),'utf8'));
  w.eval(fs.readFileSync(path.join(__dirname,'../app/static/js/barcode.js'),'utf8'));
  let code;await w.StockBridgeScan(value=>code=value);
  const input=w.document.querySelector('.scanner-photo');Object.defineProperty(input,'files',{value:[new w.File(['photo'],'barcode.jpg',{type:'image/jpeg'})]});input.dispatchEvent(new w.Event('change'));
  for(let i=0;i<300&&!code;i++)await tick();
  assert.equal(code,'5285001825028',`rotation ${rotation}: ${w.document.querySelector('.scanner-message').textContent}`);assert.equal(revoked,1);assert.equal(w.document.querySelector('dialog').open,false);
  console.log(`PASS: actual Choose File flow, supplied photograph, rotation ${rotation}°, barcode ${code}, URL released.`);w.close();
 }
})().catch(e=>{console.error(e);process.exitCode=1;});
