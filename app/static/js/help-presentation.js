(() => {
  const page = document.querySelector('[data-help-centre]');
  if (!page) return;

  const articles = [...page.querySelectorAll('[data-help-article]')];
  const topicLinks = [...page.querySelectorAll('.sb-help-topic-links a')];
  const picker = page.querySelector('[data-help-picker]');
  const search = page.querySelector('[data-help-search]');
  const input = search.querySelector('input');
  const results = search.querySelector('ul');
  const status = search.querySelector('[data-help-search-status]');
  const searchIndex = [...results.querySelectorAll('li')].map((item, index) => ({
    item, text: [...articles[index].querySelectorAll('h2, .sb-help-summary, .sb-help-steps, .sb-help-tip p')]
      .map(part => part.textContent).join(' ').toLowerCase(),
  }));

  function showTopic(id, focus = false) {
    const selected = articles.find(article => article.id === id) || articles[0];
    for (const article of articles) article.hidden = article !== selected;
    for (const link of topicLinks) {
      if (link.hash === '#' + selected.id) link.setAttribute('aria-current', 'location');
      else link.removeAttribute('aria-current');
    }
    picker.value = selected.id;
    if (focus) selected.querySelector('h2').focus();
  }

  function filterGuides() {
    const words = input.value.toLowerCase().trim().split(/\s+/).filter(Boolean);
    let count = 0;
    for (const {item, text} of searchIndex) {
      item.hidden = !words.every(word => text.includes(word));
      if (!item.hidden) count += 1;
    }
    results.hidden = words.length === 0 || count === 0;
    status.textContent = words.length === 0 ? '' : count === 0
      ? 'No guides found. Try a different search.'
      : `${count} ${count === 1 ? 'guide' : 'guides'} found`;
  }

  function openTopic(id) {
    if (window.location.hash !== '#' + id) window.history.pushState(null, '', '#' + id);
    input.value = '';
    filterGuides();
    showTopic(id, true);
  }

  page.addEventListener('click', event => {
    const link = event.target.closest('a[href^="#"]');
    if (!link || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
    const id = link.hash.slice(1);
    if (!articles.some(article => article.id === id)) return;
    event.preventDefault();
    openTopic(id);
  });
  picker.addEventListener('change', () => openTopic(picker.value));
  input.addEventListener('input', filterGuides);
  window.addEventListener('hashchange', () => showTopic(window.location.hash.slice(1)));

  for (const feedback of page.querySelectorAll('[data-help-feedback]')) {
    feedback.hidden = false;
    feedback.addEventListener('click', event => {
      const selected = event.target.closest('button');
      if (!selected) return;
      for (const button of feedback.querySelectorAll('button')) {
        button.setAttribute('aria-pressed', String(button === selected));
      }
      feedback.querySelector('[role=status]').textContent = 'Thanks. Your answer is kept on this page.';
    });
  }

  showTopic(window.location.hash.slice(1));
  search.hidden = false;
  page.dataset.helpReady = '';
})();
