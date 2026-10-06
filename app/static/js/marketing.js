// Presentation only: no requests, customer values, storage or form mutations.
(() => {
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  const journey = document.querySelector('[data-product-journey]');
  if (journey) {
    const panels = [...journey.querySelectorAll('[data-journey-panel]')];
    const steps = [...journey.querySelectorAll('[data-journey-step]')];
    steps.forEach((step, selected) => {
      step.disabled = false;
      step.addEventListener('click', () => {
        panels.forEach((panel, i) => { panel.hidden = i !== selected; });
        steps.forEach((button, i) => button.setAttribute('aria-pressed', String(i === selected)));
      });
    });
  }

  const story = document.querySelector('[data-profit-story]');
  if (story) {
    const rows = [...story.querySelectorAll('[data-profit-line]')];
    const skip = story.querySelector('[data-profit-skip]');
    let timer = null, frame = null, started = false, observer;
    const format = node => (node.dataset.prefix || '') + '₦' + Number(node.dataset.count).toLocaleString('en-NG');
    const finish = () => {
      window.clearTimeout(timer); window.cancelAnimationFrame(frame);
      rows.forEach(row => { row.setAttribute('data-revealed', 'true'); const node = row.querySelector('[data-count]'); node.textContent = format(node); });
      skip.disabled = true; skip.textContent = 'Calculation complete';
      observer?.disconnect();
    };
    const reveal = i => {
      if (i >= rows.length || reduced.matches || document.hidden) { finish(); return; }
      const row = rows[i], node = row.querySelector('[data-count]');
      row.setAttribute('data-revealed', 'true');
      const start = performance.now();
      const count = now => {
        const progress = Math.min(1, (now - start) / 450);
        node.textContent = (node.dataset.prefix || '') + '₦' + Math.round(Number(node.dataset.count) * (1 - (1 - progress) ** 3)).toLocaleString('en-NG');
        if (progress < 1) frame = window.requestAnimationFrame(count);
      };
      frame = window.requestAnimationFrame(count);
      timer = window.setTimeout(() => reveal(i + 1), 850);
    };
    skip.addEventListener('click', finish);
    if (!reduced.matches && 'IntersectionObserver' in window) {
      story.classList.add('profit-enhanced');
      skip.hidden = false;
      observer = new window.IntersectionObserver(entries => {
        if (entries[0].isIntersecting && !started) { started = true; reveal(0); }
        else if (!entries[0].isIntersecting && started) finish();
      }, {threshold: 0.25});
      observer.observe(story);
    }
    reduced.addEventListener?.('change', () => { if (reduced.matches) finish(); });
    document.addEventListener('visibilitychange', () => { if (document.hidden && started) finish(); });
  }

  const demo = document.querySelector('[data-auth-demo]');
  if (!demo) return;
  const panels = [...demo.querySelectorAll('[data-auth-panel]')];
  const steps = [...demo.querySelectorAll('[data-auth-step]')];
  const play = demo.querySelector('[data-auth-play]');
  let index = 0, timer = null, inView = true, interacting = false, playing = !reduced.matches, delay = 5500;
  const show = next => {
    index = next;
    panels.forEach((panel, i) => { panel.hidden = i !== index; });
    steps.forEach((step, i) => step.setAttribute('aria-pressed', String(i === index)));
  };
  const sync = () => {
    window.clearTimeout(timer);
    play.disabled = reduced.matches;
    play.textContent = reduced.matches ? 'Motion off' : playing ? 'Pause preview' : 'Play preview';
    if (!playing || reduced.matches || document.hidden || !inView || interacting) return;
    timer = window.setTimeout(() => { delay = 5500; show((index + 1) % panels.length); sync(); }, delay);
  };
  steps.forEach((step, i) => step.addEventListener('click', () => { delay = 7000; show(i); sync(); }));
  play.addEventListener('click', () => { playing = !playing; sync(); });
  demo.addEventListener('focusin', event => { interacting = event.target.matches(':focus-visible'); sync(); });
  demo.addEventListener('focusout', event => { if (!demo.contains(event.relatedTarget)) { interacting = false; sync(); } });
  document.addEventListener('visibilitychange', sync);
  reduced.addEventListener?.('change', () => { playing = !reduced.matches; sync(); });
  if ('IntersectionObserver' in window) {
    const observer = new window.IntersectionObserver(entries => { inView = entries[0].isIntersecting; sync(); }, {threshold: 0.15});
    observer.observe(demo);
  }
  demo.querySelector('[data-auth-controls]').hidden = false;
  show(0); sync();
})();
