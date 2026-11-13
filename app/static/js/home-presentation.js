(() => {
  const home = document.querySelector('[data-stockbridge-home]');
  if (!home) return;

  const slides = [...home.querySelectorAll('[data-home-slide]')];
  const progress = home.querySelector('[data-home-progress]');
  const controls = home.querySelector('[data-home-demo-controls]');
  const motion = home.querySelector('[data-home-motion]');
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  let index = 0;
  let paused = false;
  let animation;

  function sync() {
    animation?.cancel();
    const still = reduced.matches || paused;
    const playing = !still && !document.hidden;
    home.toggleAttribute('data-home-playing', playing);
    motion.hidden = reduced.matches;
    motion.textContent = paused ? 'Play motion' : 'Pause motion';
    if (playing && typeof progress.animate === 'function') {
      animation = progress.animate([{transform: 'scaleX(0)'}, {transform: 'scaleX(1)'}], 3000);
      animation.onfinish = next;
    }
  }

  function next() {
    index = (index + 1) % slides.length;
    slides.forEach((slide, position) => { slide.hidden = position !== index; });
    sync();
  }

  home.querySelector('[data-home-next]').addEventListener('click', next);
  motion.addEventListener('click', () => { paused = !paused; sync(); });
  reduced.addEventListener('change', sync);
  document.addEventListener('visibilitychange', sync);
  controls.hidden = false;
  sync();

  if ('IntersectionObserver' in window) {
    const observer = new IntersectionObserver(entries => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        entry.target.setAttribute('data-home-in-view', '');
        observer.unobserve(entry.target);
      }
    }, {threshold: .15});
    for (const section of home.querySelectorAll('[data-home-profit], .sb-home-features')) observer.observe(section);
  }
})();
