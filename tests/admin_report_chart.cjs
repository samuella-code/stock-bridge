// Run after test_admin_report_chart.py, passing its pytest basetemp directory.
const {chromium}=require('playwright');
const fs=require('node:fs');const path=require('node:path');const assert=require('node:assert/strict');
(async()=>{
 const base=process.argv[2];const root=path.resolve(__dirname,'..');
 const fixtures=fs.readdirSync(base).filter(p=>/^test_chart_uses_csp_safe_geome\d+$/.test(p));
 assert(fixtures.length>=4,'Need the four real Flask-rendered fixture pages');
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',args:['--no-sandbox']});
 try {for(const fixture of fixtures)for(const width of [360,768,1280]) {
  const page=await browser.newPage({viewport:{width,height:1000}});let violations=[];
  await page.addInitScript(()=>document.addEventListener('securitypolicyviolation',e=>console.error('CSP violation '+e.violatedDirective)));
  page.on('console',m=>{if(m.text().includes('CSP violation'))violations.push(m.text())});
  await page.route('**/*',async route=>{
   const url=new URL(route.request().url());
   if(url.origin!=='http://reports.invalid')return route.abort();
   if(url.pathname.startsWith('/static/')){
    const file=path.join(root,'app',url.pathname);
    if(!fs.existsSync(file))return route.fulfill({status:404,body:''});
    return route.fulfill({path:file,contentType:file.endsWith('.css')?'text/css':file.endsWith('.js')?'text/javascript':'image/svg+xml'});
   }
   return route.fulfill({body:fs.readFileSync(path.join(base,fixture,'page.html'),'utf8'),contentType:'text/html',headers:{'Content-Security-Policy':fs.readFileSync(path.join(base,fixture,'csp.txt'),'utf8')}});
  });
  await page.goto('http://reports.invalid/admin/reports');
  const result=await page.evaluate(()=>{
   const chart=document.querySelector('.report-chart').getBoundingClientRect();
   const email=document.querySelector('.report-email').getBoundingClientRect();
   const rect=document.querySelector('.report-bar rect');
   const bars=[...document.querySelectorAll('.report-bar')].map(x=>x.getBoundingClientRect());
   return {overflow:document.documentElement.scrollWidth>innerWidth,chartHeight:chart.height,emailHeight:email.height,firstBarHeight:rect.getBoundingClientRect().height,fill:getComputedStyle(rect).fill,
    sameRow:Math.abs(bars[0].top-bars[1].top)<1,statusHidden:getComputedStyle(document.querySelector('#customer-status-message')).display==='none',sideBySide:email.left>=chart.right};
  });
  assert(!result.overflow,JSON.stringify(result));assert(result.firstBarHeight>100,JSON.stringify(result));
  assert.equal(result.fill,'rgb(22, 106, 66)');assert(result.sameRow);assert(result.statusHidden);
  assert(result.emailHeight<500,JSON.stringify(result));if(width>=900)assert(result.sideBySide);
  assert.equal(violations.length,0,violations.join('\n'));
  if(fixture.endsWith('1')&&width===1280)await page.screenshot({path:process.argv[3]||'/tmp/stockbridge-chart-fixed.png',fullPage:true});
  console.log(JSON.stringify({fixture,width,...result}));await page.close();
 }}finally{await browser.close();}
})();
