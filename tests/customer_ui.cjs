// Unit checks for presentation behavior; this is not a browser/layout test.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function element(dataset = {}) {
  return {dataset, attributes: {}, handlers: {}, textContent: '',
    addEventListener(type, callback) { this.handlers[type] = callback; },
    setAttribute(key, value) { this.attributes[key] = value; },
    removeAttribute(key) { delete this.attributes[key]; }};
}
(async () => {
  const monthly = element({priceInterval: 'monthly'}), yearly = element({priceInterval: 'yearly'});
  const price = element({priceMonthly: '₦3,000/month', priceYearly: '₦30,000/year'});
  const interval = {value: 'monthly'}, submitter = element(); submitter.name = 'after_save'; submitter.value = 'another';
  const form = element(); form.action = 'https://local/products/new';
  form.querySelector = () => null;
  form.querySelectorAll = () => [submitter];
  const window = {addEventListener() {}, confirm() {throw Error('Routine action requested confirmation');}};
  const document = {getElementById() {return null;}, addEventListener() {}, querySelector() {return null;},
    querySelectorAll(selector) {
      if (selector === '[data-price-interval]') return [monthly, yearly];
      if (selector === '[data-price-monthly]') return [price];
      if (selector.includes('select[name="interval"]')) return [interval];
      if (selector.startsWith('form[method=')) return [form];
      return [];
    }};
  vm.runInNewContext(fs.readFileSync('app/static/js/customer.js', 'utf8'), {document, window, queueMicrotask});
  yearly.handlers.click();
  assert.equal(price.textContent, '₦30,000/year');
  assert.equal(interval.value, 'yearly');
  assert.equal(yearly.attributes['aria-pressed'], 'true');
  assert.equal(monthly.attributes['aria-pressed'], 'false');
  assert.equal(form.dataset.submitting, undefined); // Toggle never submits.
  const cancelled = {defaultPrevented: true}; form.handlers.submit(cancelled); await Promise.resolve();
  assert.equal(form.dataset.submitting, undefined);
  const event = {defaultPrevented: false, preventDefault() {this.defaultPrevented = true;}};
  form.handlers.submit(event); await Promise.resolve();
  assert.equal(form.dataset.submitting, 'true');
  assert.equal(form.attributes['aria-busy'], 'true');
  assert.equal(submitter.disabled, undefined); // Preserve the named submitter for Flask.
  assert.equal(submitter.value, 'another');
  form.handlers.submit(event); assert.equal(event.defaultPrevented, true);
  console.log('PASS: price-only toggle, cancelled-submit recovery, repeat-click protection, named submitter preservation (11 assertions).');
})().catch(error => {console.error(error); process.exitCode = 1;});
