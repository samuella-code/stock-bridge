// Staging only. Secrets live in Cloudflare bindings, never in source or URLs.
const URL = 'https://stock-bridge-staging-git-release-staging-phase6-ogefin.vercel.app/admin/internal/reports/weekly-dispatch';
const CONTINUE = new Set(['sent', 'failed', 'unknown', 'suppressed']);
export async function drain(env, fetcher = fetch) {
  if (typeof env.REPORT_DISPATCH_SECRET !== 'string' || env.REPORT_DISPATCH_SECRET.length < 32) throw new Error('Missing report credential');
  const results = [];
  for (let attempt = 0; attempt < 3; attempt++) {
    const response = await fetcher(URL, {
      method: 'GET', redirect: 'error', cache: 'no-store',
      headers: { Authorization: 'Bearer ' + env.REPORT_DISPATCH_SECRET },
      signal: AbortSignal.timeout(20000),
    });
    if (!response.ok) throw new Error('Report worker rejected request');
    const {result} = await response.json();
    if (!['idle','not_due','busy','disabled','configuration_required', ...CONTINUE].includes(result)) throw new Error('Unexpected report status');
    results.push(result);
    if (!CONTINUE.has(result)) break;
  }
  return results;
}
export default {
  async scheduled(controller, env) {
    const date = new Date(controller.scheduledTime);
    // Two starts on Monday: 08:00 WAT, with one recovery pass at 08:05.
    if (date.getUTCDay() !== 1 || date.getUTCHours() !== 7 || ![0,5].includes(date.getUTCMinutes())) return;
    const results = await drain(env);
    console.log(JSON.stringify({report_statuses: results}));
  },
  async fetch() { return new Response('Not found', {status: 404}); },
};
