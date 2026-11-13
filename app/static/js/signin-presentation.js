(() => {
  const story = document.querySelector('[data-signin-story]');
  if (!story) return;
  const demo = story.querySelector('[data-signin-demo]');
  const slides = [...demo.querySelectorAll('[data-signin-slide]')];
  const progress = demo.querySelector('[data-signin-progress]');
  const play = demo.querySelector('[data-signin-play]');
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  const desktop = window.matchMedia('(width >= 900px)');
  const slideDuration = 5000;
  let slide = 0;
  let animation;
  let paused = false;

  function advance() {
    slide = (slide + 1) % slides.length;
    slides.forEach((panel, index) => { panel.hidden = index !== slide; });
    sync();
  }

  function sync() {
    animation?.cancel();
    const stopped = paused || reduced.matches || document.hidden || !desktop.matches;
    story.toggleAttribute('data-signin-paused', stopped);
    play.hidden = reduced.matches;
    play.textContent = paused ? 'Play animation' : 'Pause animation';
    if (!stopped && typeof progress.animate === 'function') {
      animation = progress.animate([{transform: 'scaleX(0)'}, {transform: 'scaleX(1)'}], slideDuration);
      animation.onfinish = advance;
    }
  }

  demo.querySelector('[data-signin-next]').addEventListener('click', advance);
  play.addEventListener('click', () => { paused = !paused; sync(); });
  reduced.addEventListener('change', sync);
  desktop.addEventListener('change', sync);
  document.addEventListener('visibilitychange', sync);
  story.setAttribute('data-signin-ready', '');
  demo.querySelector('[data-signin-controls]').hidden = false;
  sync();
})();
