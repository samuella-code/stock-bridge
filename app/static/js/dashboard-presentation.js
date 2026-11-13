(() => {
  const dashboard = document.querySelector('[data-dashboard]');
  if (!dashboard) return;

  const now = new Date();
  const timeZone = 'Africa/Lagos';
  const hour = Number(new Intl.DateTimeFormat('en-GB', {
    timeZone, hour: 'numeric', hourCycle: 'h23'
  }).format(now));
  const greeting = dashboard.querySelector('[data-dashboard-greeting]');
  greeting.textContent = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';

  const date = dashboard.querySelector('[data-dashboard-date]');
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone, day: '2-digit', month: '2-digit', year: 'numeric'
  }).formatToParts(now);
  const { day, month, year } = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
  date.textContent = `${day}/${month}/${year}`;
  date.dateTime = `${year}-${month}-${day}`;

  const chartValue = dashboard.querySelector('[data-dashboard-chart-value]');
  if (!chartValue) return;
  for (const button of dashboard.querySelectorAll('[data-dashboard-chart-day]')) {
    const showValue = () => { chartValue.textContent = button.dataset.dashboardChartDay; };
    button.addEventListener('pointerenter', showValue);
    button.addEventListener('focus', showValue);
    button.addEventListener('click', showValue);
  }
})();
