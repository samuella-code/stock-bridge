(() => {
 let stream=null, scanning=false, detector=null, generation=0;
 const dialog=document.createElement('dialog');dialog.className='scanner-dialog';
 dialog.innerHTML='<h2>Scan Barcode</h2><p class="scanner-message" aria-live="polite"></p><video playsinline muted autoplay></video><label>Or enter barcode<input class="manual-barcode" maxlength="80" autocomplete="off"></label><div class="form-actions"><button type="button" class="secondary-btn scanner-close">Cancel</button><button type="button" class="primary-btn scanner-use">Use Barcode</button></div>';
 document.body.append(dialog);
 const video=dialog.querySelector('video'),message=dialog.querySelector('.scanner-message'),manual=dialog.querySelector('input');let accept=null;
 const stop=()=>{generation++;scanning=false;if(stream){stream.getTracks().forEach(t=>t.stop());stream=null;}video.srcObject=null;};
 dialog.addEventListener('close',stop);dialog.addEventListener('cancel',stop);window.addEventListener('pagehide',stop);
 dialog.querySelector('.scanner-close').onclick=()=>dialog.close();
 const finish=value=>{const code=value.trim();if(!code||code.length>80){message.textContent='Enter a barcode of 1 to 80 characters.';return;}stop();dialog.close();accept?.(code);};
 dialog.querySelector('.scanner-use').onclick=()=>finish(manual.value);
 async function frame(session){
  if(!scanning||generation!==session)return;
  try{if(video.readyState>=2){const results=await detector.detect(video);if(results.length&&scanning&&generation===session){finish(results[0].rawValue);return;}}}
  catch{message.textContent='Camera could not read this barcode. Enter it manually.';stop();return;}
  if(scanning&&generation===session)setTimeout(()=>frame(session),200);
 }
 window.StockBridgeScan=async callback=>{
  stop();const session=generation;accept=callback;manual.value='';message.textContent='You can always enter the barcode manually.';video.hidden=true;dialog.showModal();manual.focus();
  if(!window.isSecureContext||!('BarcodeDetector' in window)||!navigator.mediaDevices?.getUserMedia){message.textContent='Camera scanning is not supported here. Type the barcode or use a USB/Bluetooth scanner.';return;}
  try{
   const supported=await BarcodeDetector.getSupportedFormats();const formats=['ean_13','ean_8','upc_a','upc_e','code_128','code_39','itf'].filter(f=>supported.includes(f));
   if(!formats.length){message.textContent='Retail barcode scanning is not supported here. Enter the barcode manually.';return;}
   if(!dialog.open||generation!==session)return;
   detector=new BarcodeDetector({formats});
   const camera=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'}},audio:false});
   if(!dialog.open||generation!==session){camera.getTracks().forEach(t=>t.stop());return;}
   stream=camera;video.srcObject=stream;video.hidden=false;await video.play();scanning=true;message.textContent='Point the camera at a barcode.';frame(session);
  }catch{stop();message.textContent='Camera unavailable or permission denied. Enter the barcode manually.';}
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
