// NODE_PATH=... node tests/mobile_ui.cjs /tmp/stockbridge-dom-html
// Test-only dependency: jsdom@26.1.0.
const {JSDOM}=require('jsdom'),fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
(async()=>{
 // Mobile menu behavior against the actual Flask-rendered business page.
 const dom=new JSDOM(fs.readFileSync(path.join(process.argv[2],'products.html'),'utf8'),{url:'https://stockbridge.example/products/',runScripts:'outside-only'}),w=dom.window,d=w.document,media={matches:true,addEventListener(event,cb){this.change=cb;}};w.matchMedia=()=>media;w.eval(fs.readFileSync(path.join(root,'app/static/js/app.js'),'utf8'));
 const menu=d.querySelector('#menuButton'),sidebar=d.querySelector('#sidebar'),close=d.querySelector('#sidebarClose'),backdrop=d.querySelector('#sidebarBackdrop'),main=d.querySelector('.main-panel');assert.equal(sidebar.inert,true);
 const csrf=sidebar.querySelector('input[type="hidden"][name="csrf_token"]');assert.ok(csrf,'Logout CSRF protection remains present');
 for(const exit of ['button','backdrop','escape','link']){
  menu.click();assert.ok(sidebar.classList.contains('open'));assert.equal(backdrop.hidden,false);assert.equal(main.inert,true);assert.equal(menu.getAttribute('aria-expanded'),'true');assert.equal(d.activeElement,close);
  const last=sidebar.querySelector('.logout-btn');last.focus();d.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Tab',bubbles:true,cancelable:true}));assert.equal(d.activeElement,close);d.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Tab',shiftKey:true,bubbles:true,cancelable:true}));assert.equal(d.activeElement,last);
  if(exit==='button')close.click();if(exit==='backdrop')backdrop.click();if(exit==='escape')d.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape'}));if(exit==='link')sidebar.querySelector('a').dispatchEvent(new w.Event('click'));
  assert.equal(sidebar.classList.contains('open'),false);assert.equal(backdrop.hidden,true);assert.equal(main.inert,false);assert.equal(menu.getAttribute('aria-expanded'),'false');if(exit!=='link')assert.equal(d.activeElement,menu);
 }menu.click();media.matches=false;media.change();assert.equal(sidebar.inert,false);assert.equal(sidebar.classList.contains('open'),false);assert.equal(main.inert,false);w.close();
 console.log('PASS: mobile menu dismissal, focus trap and desktop transition.');
})().catch(error=>{console.error(error);process.exitCode=1;});
