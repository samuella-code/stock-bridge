(() => {
 let stream=null, scanning=false, detector=null, generation=0, controls=null, timer=null, decoderPromise=null, cancelPreview=null, openingSession=null, resumeOpening=false;
 const decoderUrl=new URL('vendor/zxing-browser-0.2.1.min.js',document.currentScript?.src||new URL('/static/js/barcode.js',location.href)).href;
 // DecodeHintType.TRY_HARDER = 3 in the pinned ZXing library.
 const readerHints=()=>new Map([[3,true]]);
 async function readPhoto(decoder,url,session){
  const image=new Image();
  await new Promise((resolve,reject)=>{
   const timeout=setTimeout(()=>reject(Error('PHOTO_LOAD')),15000);
   image.onload=()=>{clearTimeout(timeout);resolve();};
   image.onerror=()=>{clearTimeout(timeout);reject(Error('PHOTO_LOAD'));};
   image.src=url;
  });
  if(!image.naturalWidth||!image.naturalHeight)throw Error('PHOTO_LOAD');
  const reader=new decoder.BrowserMultiFormatOneDReader(readerHints());
  // Bound canvas memory on phones, retry different scales and orientations.
  for(const edge of [1600,1000,2200])for(const angle of [0,-5,5,-10,10,90]){
   if(!dialog.open||generation!==session)throw Error('CANCELLED');
   const scale=Math.min(1,edge/Math.max(image.naturalWidth,image.naturalHeight));
   const width=Math.round(image.naturalWidth*scale),height=Math.round(image.naturalHeight*scale);
   const radians=angle*Math.PI/180,sin=Math.abs(Math.sin(radians)),cos=Math.abs(Math.cos(radians));
   const canvas=document.createElement('canvas');canvas.width=Math.ceil(width*cos+height*sin);canvas.height=Math.ceil(height*cos+width*sin);
   const context=canvas.getContext('2d',{willReadFrequently:true});if(!context)throw Error('PHOTO_CANVAS');
   context.fillStyle='white';context.fillRect(0,0,canvas.width,canvas.height);
   context.translate(canvas.width/2,canvas.height/2);context.rotate(radians);context.drawImage(image,-width/2,-height/2,width,height);
   try{return reader.decodeFromCanvas(canvas);}catch{/* Another bounded attempt may read a tilted or distant barcode. */}
   finally{canvas.width=canvas.height=1;}
   await new Promise(resolve=>setTimeout(resolve,0));
  }
  throw Error('PHOTO_NOT_FOUND');
 }
 function loadDecoder(){
  if(window.ZXingBrowser?.BrowserMultiFormatOneDReader)return Promise.resolve(window.ZXingBrowser);
  if(!decoderPromise)decoderPromise=new Promise((resolve,reject)=>{
   const script=document.createElement('script');script.src=decoderUrl;script.async=true;let settled=false;
   const timeout=setTimeout(()=>fail(),15000);
   function fail(){if(settled)return;settled=true;clearTimeout(timeout);script.remove();decoderPromise=null;reject(Error('Scanner could not load'));}
   script.onerror=fail;script.onload=()=>{if(settled)return;clearTimeout(timeout);if(window.ZXingBrowser?.BrowserMultiFormatOneDReader){settled=true;resolve(window.ZXingBrowser);}else fail();};
   document.head.append(script);
  });
  return decoderPromise;
 }
 const dialog=document.createElement('dialog');dialog.className='scanner-dialog';
 dialog.innerHTML='<h2>Scan Barcode</h2><button type="button" class="secondary-btn scanner-close" autofocus>Close scanner</button><p class="scanner-message" aria-live="polite"></p><video playsinline muted autoplay></video><button type="button" class="secondary-btn scanner-retry" hidden>Try camera again</button><label class="scanner-photo-label">Scan a barcode photo<input type="file" class="scanner-photo" accept="image/*" capture="environment"></label><label>Or enter barcode<input class="manual-barcode" maxlength="80" autocomplete="off" inputmode="text"></label><div class="form-actions"><button type="button" class="primary-btn scanner-use">Use Barcode</button></div>';
 dialog.setAttribute('aria-label','Scan barcode');
 document.body.append(dialog);
 const video=dialog.querySelector('video'),message=dialog.querySelector('.scanner-message'),manual=dialog.querySelector('.manual-barcode');let accept=null;
 const retry=dialog.querySelector('.scanner-retry'),photo=dialog.querySelector('.scanner-photo');
 const stop=()=>{generation++;scanning=false;cancelPreview?.();cancelPreview=null;clearTimeout(timer);timer=null;const active=controls;controls=null;try{active?.stop();}catch{}if(stream){stream.getTracks().forEach(t=>t.stop());stream=null;}video.srcObject=null;video.hidden=true;};
 const dismiss=()=>{resumeOpening=false;stop();};
 dialog.addEventListener('close',dismiss);dialog.addEventListener('cancel',dismiss);window.addEventListener('pagehide',dismiss);
 document.addEventListener('visibilitychange',()=>{if(document.hidden){resumeOpening=dialog.open&&openingSession===generation;stop();retry.hidden=false;if(dialog.open)message.textContent='Camera paused. Tap Try camera again to resume.';}else if(resumeOpening&&dialog.open){resumeOpening=false;startCamera();}});
 dialog.querySelector('.scanner-close').onclick=()=>dialog.close();
 const finish=value=>{const code=String(value||'').trim();if(!code||code.length>80){message.textContent='Enter a barcode of 1 to 80 characters.';return;}const callback=accept;accept=null;stop();dialog.close();callback?.(code);};
 dialog.querySelector('.scanner-use').onclick=()=>finish(manual.value);
 function showPreview(session){
  // Set properties as well as attributes before attaching the stream. Some
  // mobile browsers do not apply the markup's muted default to playback.
  video.muted=true;video.defaultMuted=true;video.autoplay=true;video.playsInline=true;
  video.setAttribute('playsinline','');video.setAttribute('webkit-playsinline','');
  video.setAttribute('muted','');video.srcObject=stream;video.hidden=false;
  return new Promise((resolve,reject)=>{
   let settled=false,poll,deadline;
   const cancel=()=>done(Error('PREVIEW_CANCELLED'));
   function done(error){if(settled)return;settled=true;clearTimeout(poll);clearTimeout(deadline);if(cancelPreview===cancel)cancelPreview=null;error?reject(error):resolve();}
   function check(){if(settled)return;if(!dialog.open||generation!==session){done(Error('PREVIEW_CANCELLED'));return;}if(video.readyState>=2&&video.videoWidth>0&&video.videoHeight>0&&!video.paused){done();return;}poll=setTimeout(check,50);}
   cancelPreview=cancel;deadline=setTimeout(()=>done(Error('PREVIEW_TIMEOUT')),6000);
   try{Promise.resolve(video.play()).catch(()=>done(Error('PREVIEW_PLAY')));}catch{done(Error('PREVIEW_PLAY'));}
   check();
  });
 }
 async function frame(session){
  if(!scanning||generation!==session)return;
  try{if(video.readyState>=2){const results=await detector.detect(video);if(results.length&&scanning&&generation===session){finish(results[0].rawValue);return;}}}
  catch{if(scanning&&generation===session){startFallback(session);}return;}
  if(scanning&&generation===session)timer=setTimeout(()=>frame(session),200);
 }
 async function startFallback(session){
  try{
   message.textContent='Preparing camera scanner…';
   const decoder=await loadDecoder();
   if(!dialog.open||generation!==session)return;
   const reader=new decoder.BrowserMultiFormatOneDReader(readerHints(),{delayBetweenScanAttempts:200,delayBetweenScanSuccess:500});
   // Scan the already-playing video without reattaching/restarting its stream.
   const active=await reader.scan(video,(result,error,scanControls)=>{
    if(!dialog.open||generation!==session){scanControls?.stop();return;}
    if(result){scanControls?.stop();finish(result.getText());}
   });
   if(!dialog.open||generation!==session){active.stop();return;}
   controls=active;message.textContent='Point the camera at the barcode. Hold steady in good light.';
  }catch{if(dialog.open&&generation===session){stop();retry.hidden=false;message.textContent='Scanner could not start. Try again, scan a photo, or enter the barcode.';}}
 }
 async function startCamera(){
  resumeOpening=false;stop();const session=generation;openingSession=session;retry.hidden=true;retry.textContent='Try camera again';message.textContent='Opening camera…';
  if(!window.isSecureContext||!navigator.mediaDevices?.getUserMedia){openingSession=null;message.textContent='Camera scanning is not supported in this browser. Scan a photo, type the barcode, or use a USB/Bluetooth scanner.';return;}
  try{
   detector=null;
   if('BarcodeDetector' in window){try{
    const supported=await BarcodeDetector.getSupportedFormats();const formats=['ean_13','ean_8','upc_a','upc_e','code_128','code_39','itf'].filter(f=>supported.includes(f));
    if(formats.length)detector=new BarcodeDetector({formats});
   }catch{/* The bundled decoder handles browsers with incomplete native support. */}}
   if(!dialog.open||generation!==session)return;
   const camera=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'},width:{ideal:1920},height:{ideal:1080}},audio:false});
   if(!dialog.open||generation!==session){camera.getTracks().forEach(t=>t.stop());return;}
   stream=camera;await showPreview(session);
   if(!dialog.open||generation!==session)return;
   scanning=true;message.textContent='Point the camera at a barcode.';
   if(detector)frame(session);else await startFallback(session);
  }catch(error){if(dialog.open&&generation===session){stop();retry.hidden=false;
   message.textContent=error.message==='PREVIEW_PLAY'?'Camera access was allowed, but the live preview could not play. Tap Try camera again. You can also scan a photo or enter the barcode.':error.message==='PREVIEW_TIMEOUT'?'The camera did not send a live picture. Tap Try camera again, close other apps using the camera, or scan a photo.':'Camera unavailable or permission denied. Allow camera access and try again, scan a photo, or enter the barcode.';
  }}finally{if(openingSession===session)openingSession=null;}
 }
 retry.onclick=startCamera;
 photo.addEventListener('change',async()=>{
  const file=photo.files[0];if(!file)return;
  stop();const session=generation;retry.hidden=false;
  if(file.size>10*1024*1024){message.textContent='Use a barcode photo smaller than 10 MB.';photo.value='';return;}
  let url;
  try{
   message.textContent='Reading barcode photo…';const decoder=await loadDecoder();
   if(!dialog.open||generation!==session)return;
   url=URL.createObjectURL(file);
   const result=await readPhoto(decoder,url,session);
   if(dialog.open&&generation===session)finish(result.getText());
  }catch(error){if(dialog.open&&generation===session)message.textContent=error.message==='PHOTO_LOAD'?'This image could not be opened. Choose a JPG or PNG photo, or enter the barcode.':error.message==='PHOTO_CANVAS'?'This browser could not process the photo. Try another browser or enter the barcode.':error.message==='PHOTO_NOT_FOUND'?'No barcode could be read after several attempts. Crop closer to the lined barcode, keeping all bars and white margins visible, or enter the printed numbers.':'The scanner could not load. Please try again or enter the barcode.';}
  finally{if(url)URL.revokeObjectURL(url);photo.value='';}
 });
 window.StockBridgeScan=async callback=>{
  stop();accept=callback;manual.value='';photo.value='';video.hidden=true;dialog.showModal();
  await startCamera();
 };
 document.addEventListener('click',e=>{
  const button=e.target.closest('[data-scan-target]');if(!button)return;
  const input=document.querySelector(button.dataset.scanTarget);
  window.StockBridgeScan(code=>{input.value=code;input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));});
 });
 const lookup=document.querySelector('#barcode-lookup');if(!lookup)return;
 const input=document.querySelector('#barcode-value'),result=document.querySelector('#barcode-result');let request=0;
 async function find(){const code=input.value.trim();if(!code)return;const sequence=++request;result.textContent='Finding product…';
  try{const response=await fetch(`${lookup.dataset.lookup}?include_archived=1&barcode=${encodeURIComponent(code)}`,{headers:{Accept:'application/json'}});if(!response.ok)throw Error();const data=await response.json();if(sequence!==request)return;
   result.replaceChildren();const product=data.products[0];const text=document.createElement('p'),link=document.createElement('a');link.className='primary-btn button-link';
   if(product){text.textContent=`${product.name} · ${product.stock} ${product.unit} available${product.active ? '' : ' · Archived — restore this product to use it again'}`;link.textContent='Open Product';link.href=product.url;}
   else{text.textContent='No active product matches this barcode. Add its name, stock and prices.';link.textContent='Add New Product';link.href=`${lookup.dataset.create}?include_archived=1&barcode=${encodeURIComponent(code)}`;}
   result.append(text,link);
  }catch{if(sequence===request)result.textContent='Could not search products. Please try again.';}
 }
 document.querySelector('#find-barcode').onclick=find;input.addEventListener('change',find);input.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();find();}});
})();
