import assert from 'node:assert/strict';
import worker, {drain} from '../scripts/cloudflare/admin_report_scheduler.mjs';
const env = {REPORT_DISPATCH_SECRET: 'synthetic-test-credential-32-characters'};
let calls = [];
const fake = async (url, options) => {
  calls.push({url, options});
  return {ok:true,json:async()=>({result:calls.length<=2?'sent':'idle'})};
};
assert.deepEqual(await drain(env,fake), ['sent','sent','idle']);
assert.equal(calls.length,3);
assert(calls.every(c=>c.url.startsWith('https://stock-bridge-staging-git-release-staging-phase6-ogefin.vercel.app/')));
assert(calls.every(c=>c.options.redirect==='error' && c.options.headers.Authorization==='Bearer '+env.REPORT_DISPATCH_SECRET));
await assert.rejects(()=>drain({},fake));
await assert.rejects(()=>drain(env,async()=>({ok:false})));
await assert.rejects(()=>drain(env,async()=>({ok:true,json:async()=>({result:'unexpected'})})));
for (const status of ['idle','not_due','busy','disabled','configuration_required']) {
 let n=0;assert.deepEqual(await drain(env,async()=>{n++;return{ok:true,json:async()=>({result:status})}}),[status]);assert.equal(n,1);
}
assert.deepEqual(await drain(env,async()=>({ok:true,json:async()=>({result:'unknown'})})),['unknown','unknown','unknown']);
const original=globalThis.fetch;
try {
 let n=0;globalThis.fetch=async()=>{n++;return{ok:true,json:async()=>({result:'idle'})}};
 for(const time of ['2026-11-23T07:00:00Z','2026-11-23T07:05:00Z'])await worker.scheduled({scheduledTime:Date.parse(time)},env);
 assert.equal(n,2);
 for(const time of ['2026-11-22T07:00:00Z','2026-11-23T08:00:00Z','2026-11-23T07:01:00Z'])await worker.scheduled({scheduledTime:Date.parse(time)},env);
 assert.equal(n,2);assert.equal((await worker.fetch()).status,404);
} finally {globalThis.fetch=original;}
console.log('Scheduler disconnected checks passed');
