// Synthetic DOM/state tests only: no browser layout claims, network or database.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let count = 0;
function check(value, message) { assert.ok(value, message); count++; }
const assistant = fs.readFileSync('app/static/js/assistant.js', 'utf8');
const start = assistant.indexOf('  function setOpen(');
const end = assistant.indexOf("  launcher.addEventListener", start);
check(start >= 0 && end > start, 'use the actual assistant state function');
for (const navigationOpen of [false, true]) {
  let focus = '';
  const attributes = new Map();
  const root = {toggleAttribute(name, on) { if (on) attributes.set(name, ''); else attributes.delete(name); }};
  const panel = {hidden:true};
  const launcher = {hidden:false,setAttribute(name,value) {attributes.set(name,value);},focus(){focus='launcher';}};
  const input = {focus(){focus='input';}};
  const context = {root,panel,launcher,input,document:{body:{classList:{contains:()=>navigationOpen}}}};
  vm.createContext(context); vm.runInContext(assistant.slice(start,end), context);
  for (const restoreFocus of [true,false]) {
    focus='';context.setOpen(true,restoreFocus);
    check(panel.hidden === navigationOpen, 'navigation retains authority over open state');
    check(launcher.hidden === !navigationOpen, 'launcher hidden only when panel open');
    check(attributes.has('data-assistant-open') === !navigationOpen, 'CSS state follows effective open state');
    check(attributes.get('aria-expanded') === String(!navigationOpen), 'accessible state agrees');
    check(focus === (!navigationOpen ? 'input' : restoreFocus ? 'launcher' : ''), 'focus preserved');
    focus='';context.setOpen(false,restoreFocus);
    check(panel.hidden && !launcher.hidden && !attributes.has('data-assistant-open'), 'close restores flow state');
    check(focus === (restoreFocus ? 'launcher' : ''), 'close focus preserved');
  }
}
// Run the actual complete date-control script against narrow synthetic interfaces.
const dateSource = fs.readFileSync('app/static/js/report-dates.js','utf8');
function dateFixture(periodValue,from,to) {
  function field(value) { return {value,handlers:{},addEventListener(type,fn){this.handlers[type]=fn;}}; }
  const fields={period:field(periodValue),from:field(from),to:field(to)};
  const form={elements:{namedItem:name=>fields[name]}};
  vm.runInNewContext(dateSource,{document:{querySelectorAll:()=>[form]}});
  return fields;
}
for (const preset of ['today','week','month']) {
  for (const [from,to] of [['2026-10-01','2026-10-02'],['2026-10-22','2026-10-20'],['2026-10-01','']]) {
    const f=dateFixture(preset,from,to);
    check(f.period.value==='custom' && f.from.value===from && f.to.value===to, 'valid/invalid restored dates preserved');
    f.from.value='2026-10-01';f.from.handlers.input();f.to.value='2026-10-02';f.to.handlers.change();
    check(f.period.value==='custom' && f.to.value==='2026-10-02', 'correction preserves custom mode');
    f.period.value=preset;f.period.handlers.change();
    check(f.from.value==='' && f.to.value==='', 'deliberate preset clears dates');
  }
}
const customerCss=fs.readFileSync('app/static/css/customer.css','utf8');
check(customerCss.includes('.customer-app [data-report-period]>label{grid-template-columns:minmax(0,1fr)}'), 'inner grid is explicitly Reports-scoped');
const assistantCss=fs.readFileSync('app/static/css/assistant.css','utf8');
check(/@media\(max-width:600px\)\{\s*\.sb-assistant\[data-assistant-page="reports"\]:not\(\[data-assistant-open\]\)\{\s*position:static;/.test(assistantCss), 'only closed narrow Reports launcher enters flow');
check(assistantCss.includes('.sb-assistant{position:fixed;'), 'shared fixed panel/desktop rule remains');
// Check the approved rule's scope/declarations; this is not Safari rendering evidence.
const dateRule = customerCss.match(/@media\(max-width:600px\)\{\s*\.customer-app \[data-report-period\] input\[type="date"\]\{([^}]+)\}\s*\}/);
check(Boolean(dateRule), 'appearance adjustment limited to narrow Reports date controls');
for (const declaration of ['-webkit-appearance:none;', 'appearance:none;', 'box-sizing:border-box;']) {
  check(dateRule[1].includes(declaration), declaration);
}
check(!dateRule[1].includes('overflow'), 'no clipping to conceal overflow');
check(fs.readFileSync('app/templates/reports.html','utf8').includes('type="date"'), 'native date inputs retained');
console.log(`PASS: ${count} Reports mobile presentation/date/assistant assertions (synthetic; no layout engine)`);
