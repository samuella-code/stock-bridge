// DOM behavior checks; not an actual mobile browser/layout test.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('app/static/js/app.js','utf8').split('// Recover unavailable business logos without changing stored image references.')[1].split('// Keep business tables')[0];
function run(complete,naturalWidth){
 const fallback={hidden:true};
 const image={complete,naturalWidth,removed:false,parentElement:{querySelector:()=>fallback},addEventListener(type,fn){this.handler=fn;},remove(){this.removed=true;}};
 vm.runInNewContext(source,{document:{querySelectorAll:()=>[image]}});
 return {image,fallback};
}
const loaded=run(true,32);assert.equal(loaded.image.removed,false);assert.equal(loaded.fallback.hidden,true);
const pending=run(false,0);assert.equal(pending.fallback.hidden,true);pending.image.handler();assert.equal(pending.image.removed,true);assert.equal(pending.fallback.hidden,false);pending.image.handler();assert.equal(pending.fallback.hidden,false);
const broken=run(true,0);assert.equal(broken.image.removed,true);assert.equal(broken.fallback.hidden,false);
console.log('8 business avatar assertions passed');
