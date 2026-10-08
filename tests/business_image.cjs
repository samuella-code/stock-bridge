const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
let assertions=0;function equal(a,b){assert.equal(a,b);assertions++;}
const source=fs.readFileSync('app/static/js/business-image.js','utf8');
function mount(file,options={}) {
  if (file && !file.arrayBuffer) file.arrayBuffer = async () => {
    if (options.corrupt) return new Uint8Array(24).buffer;
    if (file.type === 'image/jpeg') return new Uint8Array([255,216,255,224]).buffer;
    if (file.type === 'image/png') return new Uint8Array([137,80,78,71,13,10,26,10]).buffer;
    const bytes = new Uint8Array(24);bytes.set(Buffer.from('RIFF'));bytes.set(Buffer.from('WEBP'),8);bytes.set(Buffer.from('VP8X'),12);bytes[20]=options.animated?2:0;return bytes.buffer;
  };
  const handlers={},inputHandlers={},input={files:file?[file]:[],addEventListener(k,fn){inputHandlers[k]=fn;}},status={textContent:''},preview={hidden:true},button={disabled:false};
  const form={action:'/businesses/1/logo',addEventListener(k,fn){handlers[k]=fn;},querySelector(s){return s==='input[type=file]'?input:s==='[data-image-status]'?status:s==='[data-image-preview]'?preview:button;}};
  let data=null, requests=0,destination=null,closed=false,canvas=null;
  const document={querySelectorAll(){return [form];},createElement(){canvas={getContext(){return {drawImage(){}};},toBlob(fn,mime,quality){fn({type:'image/webp',size:options.blobSize||1000});}};return canvas;}};
  const createImageBitmap=async()=>({width:options.width||1600,height:options.height||800,close(){closed=true;}});
  function FormData(){this.set=(...args)=>{data=args;};}
  vm.runInNewContext(source,{document,FormData,URL:{createObjectURL(){return 'blob:preview';},revokeObjectURL(){}},createImageBitmap,window:{location:{assign(v){destination=v;}}},fetch:async()=>{requests++;return {ok:!options.fail,url:'https://stockbridge.example/businesses/1/profile'};}});
  return {handlers,inputHandlers,input,status,preview,button,get data(){return data;},get requests(){return requests;},get destination(){return destination;},get closed(){return closed;},get canvas(){return canvas;}};
}
(async()=>{
  const event={preventDefault(){}};
  for(const type of ['image/jpeg','image/png','image/webp']) {
    const s=mount({type,size:5*1024*1024});s.inputHandlers.change();equal(s.preview.hidden,false);
    await s.handlers.submit(event);equal(s.requests,1);equal(s.canvas.width,1024);equal(s.canvas.height,512);equal(s.data[2],'business-logo.webp');equal(s.closed,true);equal(s.destination,'https://stockbridge.example/businesses/1/profile');
  }
  for(const file of [{type:'image/png',size:5*1024*1024+1},{type:'image/gif',size:100},null]) {
    const s=mount(file);await s.handlers.submit(event);equal(s.requests,0);equal(s.button.disabled,false);assert.ok(s.status.textContent.includes('5 MB'));assertions++;
  }
  let s=mount({type:'image/png',size:100},{width:5000,height:5000});await s.handlers.submit(event);equal(s.requests,0);equal(s.button.disabled,false);equal(s.closed,true);
  s=mount({type:'image/png',size:100},{fail:true});await s.handlers.submit(event);equal(s.requests,1);equal(s.button.disabled,false);equal(s.destination,null);assert.ok(s.status.textContent.includes('preserved'));assertions++;
  s=mount({type:'image/png',size:100},{corrupt:true});await s.handlers.submit(event);equal(s.requests,0);equal(s.button.disabled,false);
  s=mount({type:'image/webp',size:100},{animated:true});await s.handlers.submit(event);equal(s.requests,0);equal(s.button.disabled,false);
  console.log(`PASS: original upload boundaries, optimization dimensions, supported formats, limits, preview and failure recovery (${assertions} assertions).`);
})().catch(error=>{console.error(error);process.exitCode=1;});
