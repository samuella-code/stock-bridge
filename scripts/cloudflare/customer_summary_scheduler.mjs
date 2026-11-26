// Production customer-summary schedule. Configure CUSTOMER_SUMMARY_EMAIL_DISPATCH_SECRET
// as a Cloudflare Worker secret; never place credentials in source or request URLs.
const ENDPOINT = 'https://stock-bridge-one.vercel.app/internal/customer-email-summaries/run';

export async function run(env, fetcher = fetch) {
  if (typeof env.CUSTOMER_SUMMARY_EMAIL_DISPATCH_SECRET !== 'string'
      || env.CUSTOMER_SUMMARY_EMAIL_DISPATCH_SECRET.length < 32) {
    throw new Error('Missing customer summary dispatch credential');
  }
  const response = await fetcher(ENDPOINT, {
    method: 'POST',
    redirect: 'error',
    cache: 'no-store',
    headers: {
      Authorization: 'Bearer ' + env.CUSTOMER_SUMMARY_EMAIL_DISPATCH_SECRET,
      'Content-Type': 'application/json',
    },
    body: '{}',
    signal: AbortSignal.timeout(25000),
  });
  if (!response.ok) throw new Error('Customer summary worker rejected request');
  const result = await response.json();
  if (!Array.isArray(result.results)
      || result.results.some(value => ![
        'sent', 'retry', 'failed', 'suppressed', 'idle',
        'disabled', 'configuration_required',
      ].includes(value))) {
    throw new Error('Unexpected customer summary worker response');
  }
  return result;
}

export default {
  async scheduled(controller, env) {
    // 08:00 WAT daily (07:00 UTC); weekly summary is queued on Mondays.
    const result = await run(env);
    console.log(JSON.stringify({
      customer_summary_queued: result.queued,
      customer_summary_statuses: result.results,
    }));
  },
  async fetch() {
    return new Response('Not found', {status: 404});
  },
};
