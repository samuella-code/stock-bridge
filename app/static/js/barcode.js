(() => {
 let stream=null, scanning=false, detector=null, generation=0, controls=null, timer=null, decoderPromise=null;
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
 const stop=()=>{generation++;scanning=false;clearTimeout(timer);timer=null;const active=controls;controls=null;try{active?.stop();}catch{}if(stream){stream.getTracks().forEach(t=>t.stop());stream=null;}video.srcObject=null;video.hidden=true;};
 dialog.addEventListener('close',stop);dialog.addEventListener('cancel',stop);window.addEventListener('pagehide',stop);
 document.addEventListener('visibilitychange',()=>{if(document.hidden){stop();retry.hidden=false;if(dialog.open)message.textContent='Camera paused. Tap Try camera again to resume.';}});
 dialog.querySelector('.scanner-close').onclick=()=>dialog.close();
 const finish=value=>{const code=String(value||'').trim();if(!code||code.length>80){message.textContent='Enter a barcode of 1 to 80 characters.';return;}const callback=accept;accept=null;stop();dialog.close();callback?.(code);};
 dialog.querySelector('.scanner-use').onclick=()=>finish(manual.value);
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
   const active=await reader.decodeFromStream(stream,video,(result,error,scanControls)=>{
    if(!dialog.open||generation!==session){scanControls?.stop();return;}
    if(result){scanControls?.stop();finish(result.getText());}
   });
   if(!dialog.open||generation!==session){active.stop();return;}
   controls=active;message.textContent='Point the camera at the barcode. Hold steady in good light.';
  }catch{if(dialog.open&&generation===session){stop();retry.hidden=false;message.textContent='Scanner could not start. Try again, scan a photo, or enter the barcode.';}}
 }
 async function startCamera(){
  stop();const session=generation;retry.hidden=true;message.textContent='Opening camera…';
  if(!window.isSecureContext||!navigator.mediaDevices?.getUserMedia){message.textContent='Camera scanning is not supported in this browser. Scan a photo, type the barcode, or use a USB/Bluetooth scanner.';return;}
  try{
   detector=null;
   if('BarcodeDetector' in window){try{
    const supported=await BarcodeDetector.getSupportedFormats();const formats=['ean_13','ean_8','upc_a','upc_e','code_128','code_39','itf'].filter(f=>supported.includes(f));
    if(formats.length)detector=new BarcodeDetector({formats});
   }catch{/* The bundled decoder handles browsers with incomplete native support. */}}
   if(!dialog.open||generation!==session)return;
   const camera=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'},width:{ideal:1920},height:{ideal:1080}},audio:false});
   if(!dialog.open||generation!==session){camera.getTracks().forEach(t=>t.stop());return;}
   stream=camera;video.srcObject=stream;video.hidden=false;await video.play();
   if(!dialog.open||generation!==session)return;
   scanning=true;message.textContent='Point the camera at a barcode.';
   if(detector)frame(session);else await startFallback(session);
  }catch{if(dialog.open&&generation===session){stop();retry.hidden=false;message.textContent='Camera unavailable or permission denied. Allow camera access and try again, scan a photo, or enter the barcode.';}}
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
 function referencePreview(container,reference){
  container.replaceChildren();const heading=document.createElement('strong');heading.textContent='We found this product';container.append(heading);
  for(const value of [reference.product_name,reference.brand?`Brand: ${reference.brand}`:'',reference.variant?`Pack size: ${reference.variant}`:'','Check the details, enter your stock and prices, then save. Reference information may be incomplete or outdated.'])if(value){const p=document.createElement('p');p.textContent=value;container.append(p);}
  const note=document.createElement('small'),source=document.createElement('a'),license=document.createElement('a');source.textContent=reference.provider;source.href=reference.source_url;license.textContent='Open Database License';license.href=reference.license_url;
  for(const link of [source,license]){link.target='_blank';link.rel='noopener noreferrer';}note.append('Contains information from ',source,', available under the ',license,'.');container.append(note);
 }
 const productForm=document.querySelector('.product-form[data-recognize]');
 if(productForm){
  const barcode=productForm.querySelector('[name=barcode]'),preview=productForm.querySelector('#product-recognition');let sequence=0;
  async function identify(){const code=barcode.value.trim(),mine=++sequence;if(!code){preview.textContent='Scan or enter a barcode first.';return;}preview.textContent='Trying to identify this product…';
   try{const response=await fetch(`${productForm.dataset.recognize}?barcode=${encodeURIComponent(code)}`,{headers:{Accept:'application/json'}});if(!response.ok)throw Error();const data=await response.json();if(mine!==sequence||barcode.value.trim()!==code)return;
    if(data.products?.length){preview.replaceChildren();const text=document.createElement('p'),link=document.createElement('a');text.textContent=`Product already exists: ${data.products[0].name}${data.products[0].active?'':' (archived)'}.`;link.textContent='View Product';link.href=data.products[0].url;preview.append(text,link);return;}
    if(data.reference){referencePreview(preview,data.reference);for(const [field,value] of Object.entries({name:data.reference.product_name,category:data.reference.category,description:data.reference.description})){const input=productForm.querySelector(`[name=${field}]`);if(input&&!input.value.trim())input.value=value||'';}}
    else preview.textContent="We couldn't automatically identify this product. You can still add it manually. We've saved the barcode for you.";
   }catch{if(mine===sequence&&barcode.value.trim()===code)preview.textContent='Product lookup is unavailable. You can still enter the details and save manually.';}
  }
  productForm.querySelector('.identify-product').onclick=identify;barcode.addEventListener('change',identify);barcode.addEventListener('input',()=>sequence++);
 }
 const lookup=document.querySelector('#barcode-lookup');if(!lookup)return;
 const input=document.querySelector('#barcode-value'),result=document.querySelector('#barcode-result');let request=0;
 async function find(){const code=input.value.trim();if(!code)return;const sequence=++request;result.textContent='Finding product…';
  try{const response=await fetch(`${lookup.dataset.lookup}?include_archived=1&barcode=${encodeURIComponent(code)}`,{headers:{Accept:'application/json'}});if(!response.ok)throw Error();const data=await response.json();if(sequence!==request)return;
   result.replaceChildren();const product=data.products[0];const text=document.createElement('p'),link=document.createElement('a');link.className='primary-btn button-link';
   if(product){text.textContent=`${product.name} · ${product.stock} ${product.unit} available${product.active ? '' : ' · Archived — restore this product to use it again'}`;link.textContent='Open Product';link.href=product.url;}
   else{if(data.reference){referencePreview(result,data.reference);text.textContent='Review this product and enter your stock and prices.';link.textContent='Review & Add Product';}else{text.textContent="We couldn't automatically identify this product. You can still add it manually. We've saved the barcode for you.";link.textContent='Add New Product';}link.href=`${lookup.dataset.create}?barcode=${encodeURIComponent(code)}`;}
   result.append(text,link);
  }catch{if(sequence===request)result.textContent='Could not search products. Please try again.';}
 }
 document.querySelector('#find-barcode').onclick=find;input.addEventListener('change',find);input.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();find();}});
})();
