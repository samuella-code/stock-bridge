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
  let index = 0, timer = null, inView = true, hovered = false;
  let playing = !reduced.matches && !compact.matches;
  const stopTimer = () => { if (timer !== null) window.clearTimeout(timer); timer = null; };
  function sync() {
    stopTimer();
    play.disabled = reduced.matches || compact.matches;
    play.textContent = reduced.matches ? 'Motion off · choose a step' : compact.matches ? 'Explore at your pace' :
      playing ? 'Pause showcase' : index === panels.length - 1 ? 'Replay showcase' : 'Play showcase';
    if (!playing || reduced.matches || compact.matches || document.hidden || !inView || hovered) return;
    timer = window.setTimeout(() => {
      timer = null;
      if (index < panels.length - 1) show(index + 1);
      else playing = false; // One pass only; never loop or restart on scroll.
      sync();
    }, 3600);
  }
  function show(next, manual = false) {
    index = next;
    panels.forEach((panel, i) => { panel.hidden = i !== index; });
    steps.forEach((step, i) => step.setAttribute('aria-pressed', String(i === index)));
    root.querySelectorAll('[data-showcase-side]').forEach((item, i) => {
      item.setAttribute('data-active', String(i === [0, 1, 3, 2, 5, 6][index]));
    });
    const panel = panels[index];
    ['sales', 'profit', 'stock'].forEach(metric => {
      root.querySelector(`[data-showcase-${metric}]`).textContent = panel.dataset[metric];
    });
    caption.textContent = `${String(index + 1).padStart(2, '0')} / 06 · ${panel.querySelector('h3').textContent}`;
    if (manual) announcement.textContent = `Step ${index + 1} of 6: ${panel.querySelector('h3').textContent}`;
  }
  steps.forEach((step, i) => step.addEventListener('click', () => {
    playing = false; show(i, true); sync();
  }));
  play.addEventListener('click', () => {
    if (reduced.matches || compact.matches) return;
    playing = !playing;
    if (playing && index === panels.length - 1) show(0, true);
    sync();
  });
  root.addEventListener('focusin', event => {
    if (event.target !== play) { playing = false; sync(); }
  });
  root.addEventListener('pointerenter', event => { if (event.pointerType === 'mouse') { hovered = true; sync(); } });
  root.addEventListener('pointerleave', event => { if (event.pointerType === 'mouse') { hovered = false; sync(); } });
  document.addEventListener('visibilitychange', sync);
  const preferenceChanged = () => { if (reduced.matches || compact.matches) playing = false; sync(); };
  reduced.addEventListener?.('change', preferenceChanged);
  compact.addEventListener?.('change', preferenceChanged);
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
