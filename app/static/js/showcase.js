// Homepage-only illustration. No API calls, real forms, customer data or writes.
(() => {
  const root = document.querySelector('[data-showcase]');
  if (!root || !window.matchMedia) return;
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  const compact = window.matchMedia('(max-width: 768px)');
  const panels = [...root.querySelectorAll('[data-showcase-panel]')];
  const steps = [...root.querySelectorAll('[data-showcase-step]')];
  const play = root.querySelector('[data-showcase-play]');
  const caption = root.querySelector('[data-showcase-caption]');
  const announcement = root.querySelector('[data-showcase-announcement]');
  const interval = 4500, manualDelay = 7000;
  let index = 0, timer = null, inView = true, hovered = false, focused = false;
  let nextDelay = interval;
  let playing = !reduced.matches;
  const stopTimer = () => { if (timer !== null) window.clearTimeout(timer); timer = null; };
  function sync() {
    stopTimer();
    play.disabled = reduced.matches;
    play.textContent = reduced.matches ? 'Motion off · choose a step' :
      playing ? 'Pause showcase' : 'Play showcase';
    if (!playing || reduced.matches || document.hidden || !inView || hovered || focused) return;
    timer = window.setTimeout(() => {
      timer = null;
      nextDelay = interval;
      show((index + 1) % panels.length);
      sync();
    }, nextDelay);
  }

  function show(next, manual = false) {
    index = next;
    panels.forEach((panel, i) => { panel.hidden = i !== index; });
    steps.forEach((step, i) => step.setAttribute('aria-pressed', String(i === index)));
    root.querySelectorAll('[data-showcase-side]').forEach((item, i) => {
      item.setAttribute('data-active', String(i === [0, 1, 3, 2, 4, 5, 6][index]));
    });
    const panel = panels[index];
    ['sales', 'profit', 'stock'].forEach(metric => {
      root.querySelector(`[data-showcase-${metric}]`).textContent = panel.dataset[metric];
    });
    const profitLabel = root.querySelector('[data-showcase-profit-label]');
    if (profitLabel) profitLabel.textContent = panel.dataset.metricLabel || 'Net profit';
    const expenses = root.querySelector('[data-showcase-expenses]');
    if (expenses) expenses.textContent = index >= 5 ? '₦500' : '₦0';
    caption.textContent = `${String(index + 1).padStart(2, '0')} / ${String(panels.length).padStart(2, '0')} · ${panel.querySelector('h3').textContent}`;
    if (manual) announcement.textContent = `Step ${index + 1} of ${panels.length}: ${panel.querySelector('h3').textContent}`;
  }
  steps.forEach((step, i) => step.addEventListener('click', () => {
    nextDelay = manualDelay; show(i, true); sync();
  }));
  play.addEventListener('click', () => {
    if (reduced.matches) return;
    playing = !playing;
    sync();
  });
  // Keyboard interaction pauses without moving focus. Touch selection keeps cycling.
  root.addEventListener('focusin', event => {
    focused = event.target.matches(':focus-visible'); sync();
  });
  root.addEventListener('focusout', event => {
    if (!root.contains(event.relatedTarget)) { focused = false; sync(); }
  });
  root.addEventListener('pointerenter', event => {
    if (event.pointerType === 'mouse' && !compact.matches) { hovered = true; sync(); }
  });
  root.addEventListener('pointerleave', event => {
    if (event.pointerType === 'mouse') { hovered = false; sync(); }
  });
  document.addEventListener('visibilitychange', sync);
  reduced.addEventListener?.('change', () => { playing = !reduced.matches; sync(); });
  compact.addEventListener?.('change', () => { hovered = false; sync(); });
  if ('IntersectionObserver' in window) {
    const viewObserver = new window.IntersectionObserver(entries => {
      inView = entries[0].isIntersecting; sync();
    }, {threshold: 0.15});
    viewObserver.observe(root);
  }
  root.querySelector('[data-showcase-controls]').hidden = false;
  show(0); sync();

  // Progressive enhancement: sections stay visible without JS/observer support.
  // Reveals happen once; keyboard focus and reduced motion expose content instantly.
  if (!('IntersectionObserver' in window)) return;
  const sections = [...document.querySelectorAll('.entry-home .entry-section, .entry-home .entry-final')];
  const reveal = section => { section.classList.remove('reveal-pending'); observer.unobserve(section); };
  const observer = new window.IntersectionObserver(entries => {
    entries.forEach(entry => { if (entry.isIntersecting) reveal(entry.target); });
  }, {threshold: 0.08});
  if (!reduced.matches) sections.forEach(section => {
    section.classList.add('section-reveal', 'reveal-pending');
    section.addEventListener('focusin', () => reveal(section));
    observer.observe(section);
  });
  reduced.addEventListener?.('change', () => {
    if (reduced.matches) { sections.forEach(reveal); observer.disconnect(); }
  });
})();
