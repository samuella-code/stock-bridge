const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
let assertions=0;const eq=(a,b)=>{assert.deepEqual(a,b);assertions++;};
const inputs=Array.from({length:5},(_,i)=>({id:`password-${i}`,type:'password',name:i===4?'confirm_password':'password',value:'synthetic-only',autocomplete:'new-password',required:true,minLength:8}));
const buttons=inputs.map(input=>({hidden:true,type:'button',dataset:{customerPasswordToggle:input.id},attrs:{},icons:{'[data-eye-open]':{hidden:false},'[data-eye-off]':{hidden:true}},setAttribute(k,v){this.attrs[k]=v;},querySelector(s){return this.icons[s];},addEventListener(k,f){this[k]=f;}}));
const before=inputs.map(i=>({...i}));
vm.runInNewContext(fs.readFileSync('app/static/js/password_visibility.js','utf8'),{document:{querySelectorAll:()=>buttons,getElementById:id=>inputs.find(i=>i.id===id),querySelector:()=>({textContent:'New password'})}});
buttons.forEach((b,i)=>{eq(b.hidden,false);eq(inputs[i].type,'password');b.click();eq(inputs[i].type,'text');eq(b.attrs['aria-label'],'Hide new password');eq(b.attrs['aria-pressed'],'true');eq(b.icons['[data-eye-off]'].hidden,false);inputs.forEach((input,j)=>{if(i!==j)eq(input.type,'password');});b.click();eq(inputs[i].type,'password');eq(b.attrs['aria-label'],'Show new password');eq(b.attrs['aria-pressed'],'false');eq(inputs[i],before[i]);eq(b.type,'button');});
console.log(`PASS: customer password visibility (${assertions} assertions)`);
