(() => {
  const date = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Africa/Lagos', day: '2-digit', month: '2-digit', year: 'numeric'
  });
  const time = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Africa/Lagos', hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
  });

  for (const element of document.querySelectorAll('time[data-sb-wat]')) {
    const instant = new Date(element.dateTime);
    if (Number.isNaN(instant.valueOf())) continue;
    const localDate = date.format(instant);
    element.textContent = element.dataset.sbWat === 'time'
      ? `${localDate} · ${time.format(instant)} WAT`
      : localDate;
  }
})();
