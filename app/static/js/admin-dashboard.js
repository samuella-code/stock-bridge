const platformSearch = document.getElementById('platform-search');
if (platformSearch) {
  document.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      platformSearch.focus();
    }
  });
}
