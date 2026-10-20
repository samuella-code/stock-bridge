// Dependency-free DOM contract tests; not a native browser verification.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
let assertions = 0;
function check(value) { assert.ok(value); assertions++; }
const input = {type: 'password', value: 'synthetic-password'};
const attrs = {};
const button = {dataset: {passwordToggle: 'password'},
  addEventListener(type, fn) { this[type] = fn; },
  setAttribute(name, value) { attrs[name] = value; }};
const summary = {focused: false, focus() {this.focused = true;}};
const menu = {open: true, addEventListener(type, fn) {this[type] = fn;},
  querySelector(selector) {assert.equal(selector, 'summary'); return summary;}};
const document = {getElementById() {return input;}, querySelectorAll(selector) {
  if (selector === '[data-password-toggle]') return [button];
  if (selector === '.mobile-admin-menu') return [menu];
  throw new Error('Unexpected selector');
}};
vm.runInNewContext(fs.readFileSync('app/static/js/admin.js', 'utf8'), {document});
check(input.type === 'password');
button.click();
check(input.type === 'text'); check(input.value === 'synthetic-password');
check(button.textContent === 'Hide'); check(attrs['aria-label'] === 'Hide password'); check(attrs['aria-pressed'] === 'true');
button.click();
check(input.type === 'password'); check(input.value === 'synthetic-password');
check(button.textContent === 'Show'); check(attrs['aria-pressed'] === 'false');
let prevented = false;
menu.keydown({key: 'Enter', preventDefault() {prevented = true;}});
check(menu.open); check(!prevented);
menu.keydown({key: 'Escape', preventDefault() {prevented = true;}});
check(!menu.open); check(prevented); check(summary.focused);
summary.focused = false; prevented = false;
menu.keydown({key: 'Escape', preventDefault() {prevented = true;}});
check(!prevented); check(!summary.focused);
console.log(`${assertions} admin UI assertions passed`);
