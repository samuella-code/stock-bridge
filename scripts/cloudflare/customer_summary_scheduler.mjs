// Production customer-summary schedule. Configure CUSTOMER_SUMMARY_EMAIL_DISPATCH_SECRET
// as a Cloudflare Worker secret; never place credentials in source or request URLs.
const ENDPOINT = 'https://stock-bridge-one.vercel.app/internal/customer-email-summaries/run';
const STOP = new Set(['idle', 'disabled', 'configuration_required']);

export async function run(env, fetcher = fetch) {
  if (typeof env.CUSTOMER_SUMMARY_EMAIL_DISPATCH_SECRET !== 'string'
      || env.CUSTOMER_SUMMARY_EMAIL_DISPATCH_SECRET.length < 32) {
    throw new Error('Missing customer summary dispatch credential');
  }
  const results = [];
  let queued = 0;
  // Each request sends at most one email; three per scheduled invocation keeps
  // SMTP work outside a long-running serverless request.
  for (let attempt = 0; attempt < 3; attempt++) {
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
    if (typeof result.result !== 'string' || ![
      'sent', 'retry', 'failed', 'suppressed', 'idle',
      'disabled', 'configuration_required',
    ].includes(result.result)) {
      throw new Error('Unexpected customer summary worker response');
    }
    queued += Number.isInteger(result.queued) ? result.queued : 0;
    results.push(result.result);
    if (STOP.has(result.result)) break;
  }
  return {queued, results};
}

export default {
  async scheduled(controller, env) {
    // Cron: every 5 minutes from 07:00–10:55 UTC (08:00–11:55 WAT).
    // The first run queues due summaries; later runs drain the remaining outbox.
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
