const adminCode = document.querySelector('[data-admin-code]');

if (adminCode) {
  const group = document.createElement('div');
  group.className = 'admin-code-boxes';
  group.setAttribute('role', 'group');
  group.setAttribute('aria-labelledby', 'admin-mfa-code-label');

  const boxes = Array.from({ length: 6 }, (_, index) => {
    const input = document.createElement('input');
    input.type = 'text';
    input.inputMode = 'numeric';
    input.pattern = '[0-9]';
    input.required = true;
    input.autocomplete = index === 0 ? 'one-time-code' : 'off';
    input.setAttribute('aria-label', `Digit ${index + 1} of 6`);
    group.append(input);
    return input;
  });

  function syncCode() {
    adminCode.value = boxes.map(box => box.value).join('');
  }

  function enterDigits(value, index) {
    const digits = value.replace(/[^0-9]/g, '');
    if (digits.length === boxes.length) index = 0;
    if (!digits) {
      boxes[index].value = '';
      syncCode();
      return;
    }
    const available = digits.slice(0, boxes.length - index);
    [...available].forEach((digit, offset) => {
      boxes[index + offset].value = digit;
    });
    boxes[Math.min(index + available.length, boxes.length - 1)].focus();
    syncCode();
  }

  boxes.forEach((box, index) => {
    box.addEventListener('input', () => enterDigits(box.value, index));
    box.addEventListener('focus', () => box.select());
    box.addEventListener('paste', event => {
      event.preventDefault();
      enterDigits(event.clipboardData.getData('text'), index);
    });
    box.addEventListener('keydown', event => {
      if (event.key === 'Backspace' && !box.value && index > 0) {
        event.preventDefault();
        boxes[index - 1].value = '';
        boxes[index - 1].focus();
        syncCode();
      }
    });
  });

  adminCode.after(group);
  document.getElementById('admin-mfa-code-label').removeAttribute('for');
  adminCode.type = 'hidden';
  adminCode.form.addEventListener('submit', syncCode);
}
