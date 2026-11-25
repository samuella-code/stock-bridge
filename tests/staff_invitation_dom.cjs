// Actual invitation script in a synthetic DOM. No browser/network assertions.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('app/static/js/staff.js','utf8');
let count=0;
function check(value){assert.ok(value);count++;}
for(const token of ['A'.repeat(43),'short','A'.repeat(44),'bad!'.repeat(11),'']){
 const field={value:''};const button={disabled:true};const history=[];
 const form={elements:{namedItem:()=>field},querySelector:()=>button};
 const link={value:'/staff/invite#token='+'B'.repeat(43)};
 let copyClickHandler;
 const copyButton={addEventListener:(event,handler)=>{check(event==='click'&&typeof handler==='function');copyClickHandler=handler;}};
 const context={URL,URLSearchParams,document:{querySelector:s=>s==='[data-invite-link]'?link:s==='[data-copy-staff-invite]'?copyButton:form},window:{location:{protocol:'https:',origin:'https://preview.invalid',pathname:'/staff/invite',hash:'#token='+token},history:{replaceState:(...args)=>history.push(args)}}};
 vm.runInNewContext(source,context);
 check(typeof copyClickHandler==='function');
 check(link.value.startsWith('https://preview.invalid/staff/invite#token='));
 check(history.length===1 && history[0][2]==='/staff/invite');
 check(button.disabled===(token!=='A'.repeat(43)));
 check(field.value===(token==='A'.repeat(43)?token:''));
}
for(const forbidden of ['localStorage','sessionStorage','fetch(','XMLHttpRequest','innerHTML','console.'])check(!source.includes(forbidden));
console.log(`PASS: ${count} invitation fragment/secret-handling assertions (synthetic DOM)`);

const fileLink={value:'/staff/invite#token='+'A'.repeat(43)};
vm.runInNewContext(source,{URL,URLSearchParams,document:{querySelector:s=>s==='[data-invite-link]'?fileLink:null},window:{location:{protocol:'file:',origin:'null'}}});
check(fileLink.value.startsWith('/staff/invite#token='));
console.log('PASS: local-file preview does not attempt an invalid origin conversion');


// Staff invitation presentation contract: staff-facing warnings are removed,
// admin reminder delivery guidance remains, and invitation controls stay visible.
const manageTemplate = fs.readFileSync('app/templates/staff/manage.html','utf8');
const teamTemplate = fs.readFileSync('app/templates/staff/team_manage.html','utf8');
const remindersTemplate = fs.readFileSync('app/templates/admin/reminders.html','utf8');
for (const template of [manageTemplate, teamTemplate]) {
  check(!/Email (?:sending|delivery) is off|needs separate approval|until approved|site administrator to check email delivery/i.test(template));
  check(template.includes('The employee gets a link to accept. You can also copy the link and send it yourself.'));
  check(template.includes('Copy invite link'));
  check(template.includes('Resend invitation'));
}
check(manageTemplate.includes('An expired invitation does not use a seat.'));
check(manageTemplate.includes('without using an extra seat'));
check(teamTemplate.includes('An invitation holds a seat until it is accepted or expires.'));
check(teamTemplate.includes('Resending replaces the old link and does not use another seat.'));
check(teamTemplate.includes('New employees verify their own email.'));
check(remindersTemplate.includes('Email delivery needs separate approval and configuration. No message will be sent.'));
check(!manageTemplate.includes('email_labels.get(invitation.delivery_status'));
console.log('PASS: staff invitation presentation contract and admin reminder warning preservation');

// Route passes only the freshly generated secret and matching invitation identity
// to the private, no-store success response on both staff invitation surfaces.
const staffRoutes = fs.readFileSync('app/staff/routes.py','utf8');
check(staffRoutes.includes("fresh_invite_token=raw, invited_email=invitation.email, invitation_action=action"));
check(staffRoutes.includes("fresh_invite_token=raw,invited_email=row.email,invitation_action=action"));
check(teamTemplate.includes("Invitation created for {{invited_email}}."));
check(teamTemplate.includes("Invitation link refreshed for {{invited_email}}."));
check(manageTemplate.includes("Invitation created for {{ invited_email }}."));
console.log('PASS: fresh invitation identity and token are wired to both private success views');


// Team-management copy control: exercise both Clipboard API success and
// the manual-select fallback used when clipboard access is unavailable.
async function runTeamCopy(clipboardWrite) {
  let handler;
  const input={value:'/staff/invite#token='+ 'C'.repeat(43),type:'hidden',focused:false,selected:false,
    focus(){this.focused=true;},select(){this.selected=true;}};
  const status={textContent:''};
  const card={querySelector:selector=>selector==='[data-copy-status]'?status:null};
  const button={addEventListener:(event,fn)=>{check(event==='click');handler=fn;},
    closest:selector=>selector==='.team-card'?card:null};
  const context={URL,location:{origin:'https://preview.invalid'},navigator:{clipboard:{writeText:clipboardWrite}},
    document:{querySelectorAll:selector=>selector==='[data-team-tabs] a'?[]:selector==='[data-team-panel]'?[]:selector==='[data-copy-team-invite]'?[button]:[],
      querySelector:selector=>selector==='#team-invite-link'?input:null}};
  vm.runInNewContext(fs.readFileSync('app/static/js/staff-team.js','utf8'),context);
  check(typeof handler==='function');
  await handler();
  return {input,status};
}
(async()=>{
  const copied=[];
  const success=await runTeamCopy(async value=>{copied.push(value);});
  check(copied.length===1 && copied[0]==='https://preview.invalid/staff/invite#token='+'C'.repeat(43));
  check(success.status.textContent==='Invite link copied. Only share it with the invited person.');
  const fallback=await runTeamCopy(async()=>{throw new Error('clipboard unavailable');});
  check(fallback.input.type==='text' && fallback.input.focused && fallback.input.selected);
  check(fallback.status.textContent==='Select and copy the invitation link above.');
  console.log('PASS: team invitation copy control and manual fallback');
})().catch(error=>{console.error(error);process.exitCode=1;});
