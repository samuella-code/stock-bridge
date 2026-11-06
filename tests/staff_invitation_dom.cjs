// Actual invitation script in a synthetic DOM. No browser/network assertions.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('app/static/js/staff.js','utf8');
let count=0;
function check(value){assert.ok(value);count++;}
for(const token of ['A'.repeat(43),'short','A'.repeat(44),'bad!'.repeat(11),'']){
 const field={value:''};const button={disabled:true};const history=[];
 const form={elements:{namedItem:()=>field},querySelector:()=>button};
 const link={value:'/staff/invite#token='+'B'.repeat(43)};
 const context={URL,URLSearchParams,document:{querySelector:s=>s==='[data-invite-link]'?link:form},window:{location:{protocol:'https:',origin:'https://preview.invalid',pathname:'/staff/invite',hash:'#token='+token},history:{replaceState:(...args)=>history.push(args)}}};
 vm.runInNewContext(source,context);
 check(link.value.startsWith('https://preview.invalid/staff/invite#token='));
 check(history.length===1 && history[0][2]==='/staff/invite');
 check(button.disabled===(token!=='A'.repeat(43)));
 check(field.value===(token==='A'.repeat(43)?token:''));
}
for(const forbidden of ['localStorage','sessionStorage','fetch(','XMLHttpRequest','innerHTML','console.'])check(!source.includes(forbidden));
console.log(`PASS: ${count} invitation fragment/secret-handling assertions (synthetic DOM)`);

const fileLink={value:'/staff/invite#token='+'A'.repeat(43)};
vm.runInNewContext(source,{URL,URLSearchParams,document:{querySelector:s=>s==='[data-invite-link]'?fileLink:null},window:{location:{protocol:'file:',origin:'null'}}});
check(fileLink.value.startsWith('/staff/invite#token='));
console.log('PASS: local-file preview does not attempt an invalid origin conversion');
