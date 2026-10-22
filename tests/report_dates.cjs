// Offline DOM tests: no server, network, or database.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const source = fs.readFileSync('app/static/js/report-dates.js', 'utf8');
let checks = 0;
function equal(actual, expected) { assert.equal(actual, expected); checks++; }
function setup(period = 'month', from = '', to = '') {
  const dom = new JSDOM(`<form data-report-period>
    <select name="period"><option>today</option><option>week</option><option>month</option><option>custom</option></select>
    <input type="date" name="from"><input type="date" name="to"><button>Show report</button>
    </form><form id="dashboard"><select name="period"><option>month</option></select><input type="date" name="from" value="2026-10-01"></form>`, {runScripts:'outside-only'});
  const form = dom.window.document.querySelector('[data-report-period]');
  const fields = form.elements;
  fields.period.value = period; fields.from.value = from; fields.to.value = to;
  dom.window.eval(source);
  return {dom, fields, form, fire: (node, type) => node.dispatchEvent(new dom.window.Event(type, {bubbles:true}))};
}
for (const preset of ['today', 'week', 'month']) {
  for (const field of ['from', 'to']) {
    for (const event of ['input', 'change']) {
      const {dom, fields, fire} = setup(preset);
      fields[field].value = '2026-10-22'; fire(fields[field], event);
      equal(fields.period.value, 'custom'); equal(fields[field].value, '2026-10-22');
      dom.window.close();
    }
  }
  const {dom, fields, form, fire} = setup('custom', '2026-10-22', '2026-10-20');
  fields.period.value = preset; fire(fields.period, 'change');
  const submitted = new dom.window.FormData(form);
  equal(submitted.get('period'), preset); equal(submitted.get('from'), ''); equal(submitted.get('to'), '');
  equal(dom.window.document.querySelector('#dashboard input').value, '2026-10-01');
  dom.window.close();
}
for (const values of [['2026-10-22',''], ['', '2026-10-20'], ['2026-10-22','2026-10-20']]) {
  const {dom, fields, fire} = setup('month', ...values);
  equal(fields.period.value, 'custom'); equal(fields.from.value, values[0]); equal(fields.to.value, values[1]);
  fire(fields.period, 'change'); equal(fields.from.value, values[0]); equal(fields.to.value, values[1]);
  dom.window.close();
}
const empty = new JSDOM('<form data-report-period></form>', {runScripts:'outside-only'});
assert.doesNotThrow(() => empty.window.eval(source)); checks++;
empty.window.close();
console.log(`${checks} Reports date-control assertions passed`);
