"""Admin notifications and explicitly approved email reminders; no live defaults."""
import secrets
from datetime import datetime
from hmac import compare_digest

import click
from flask import abort, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app import db
from app.admin.routes import admin_bp, admin_required
from app.admin import reminder_service as service, notification_data
from app.admin.customer_data import masked_email, local_time
from app.models import (AdminAlertPreference, AdminReminderBatch, AdminReminderDelivery,
    AdminReminderFeedback, AdminReminderSchedule, CustomerReminderPreference, User)
from app.staff.service import lock_owner


def actor():
    user=db.session.get(User,current_user.id,populate_existing=True)
    if not service.admin_ready(user,auth_version=session.get('admin_auth_version'),mfa_verified=session.get('admin_mfa_verified') is True):abort(403)
    return user


def version(name):
    value=request.form.get(name,'')
    if not value.isdigit() or len(value)>9:abort(400)
    return int(value)


def switch(name):
    value=request.form.get(name,'')
    if value not in ('','on'):abort(400)
    return value=='on'


@admin_bp.post('/notifications/preferences')
@admin_required
def notification_preferences():
    user=actor();lock_owner(user.id);user=actor()
    row=db.session.get(AdminAlertPreference,user.id)
    if version('version')!=(row.version if row else 0):abort(409)
    if row is None:row=AdminAlertPreference(user_id=user.id,version=0);db.session.add(row)
    for key in ('failed_payments','security_alerts','daily_summary'):setattr(row,key,switch(key))
    stopped=db.session.get(CustomerReminderPreference,user.id)
    if stopped and any(getattr(row,key) for key in ('failed_payments','security_alerts','daily_summary')):
        stopped.stopped_at=None  # Explicit admin opt-in restores only this admin's optional alerts.
    row.version+=1;row.auth_version=user.admin_auth_version;row.mfa_verified=session.get('admin_mfa_verified') is True
    service.audit('ADMIN_ALERT_PREFERENCE_UPDATED',user.id,user.id,'Saved admin email alert choices.')
    db.session.commit();flash('Alert preferences saved. Delivery still requires separate approval.','success')
    return redirect(url_for('admin.notifications'),303)


@admin_bp.post('/reminders/schedules')
@admin_required
def reminder_schedules():
    user=actor();lock_owner(user.id);user=actor()
    keys=[kind+':'+key for kind,values in service.TIMINGS.items() for key,label in values]
    # Validate every version before changing any row.
    rows={r.key:r for r in AdminReminderSchedule.query.populate_existing().with_for_update().all()}
    for key in keys:
        if version('version_'+key)!=(rows[key].version if key in rows else 0):abort(409)
        switch(key)
    actor_id,auth_version=user.id,user.admin_auth_version
    for key in keys:
        row=rows.get(key)
        if row is None:row=AdminReminderSchedule(key=key,version=0);db.session.add(row)
        row.enabled=switch(key);row.version+=1;row.actor_id=actor_id
        row.auth_version=auth_version;row.mfa_verified=session.get('admin_mfa_verified') is True
    service.audit('ADMIN_REMINDER_SCHEDULE_UPDATED',user.id,user.id,'Saved the six reminder timing choices; seven-day cap still applies.')
    try:db.session.commit()
    except IntegrityError:
        db.session.rollback();abort(409,description='Reminder timings changed. Refresh before saving again.')
    flash('Reminder timings saved. Automatic delivery still requires separate approval.','success')
    return redirect(url_for('admin.notifications'),303)


@admin_bp.route('/reminders/<kind>',methods=['GET','POST'])
@admin_required
def reminder_panel(kind):
    if kind not in service.TIMINGS:abort(404)
    now=datetime.utcnow();user=actor()
    if request.method=='POST':
        action=request.form.get('action')
        if action not in ('send','test'):abort(400)
        raw=request.form.getlist('owners')
        if any(not v.isdigit() or len(v)>10 for v in raw):abort(400)
        ids=[user.id] if action=='test' else [int(v) for v in raw]
        override=switch('override')
        reason=request.form.get('override_reason','') if override else ''
        if override and not reason.strip():abort(400)
        try:
            batch,new=service.claim(user,request.form.get('request_key',''),kind,ids,request.form.get('message',''),
                override_reason=reason,feedback=switch('feedback') and action!='test',is_test=action=='test',
                auth_version=session.get('admin_auth_version'),mfa_verified=session.get('admin_mfa_verified') is True,now=now)
            if new:service.deliver(batch.id,auth_version=user.admin_auth_version,mfa_verified=session.get('admin_mfa_verified') is True,now=now)
        except ValueError as error:
            db.session.rollback();return render_panel(kind,now,error=str(error)),409
        except IntegrityError:
            db.session.rollback();return render_panel(kind,now,error='This request is already being processed. Check Activity before trying again.'),409
        return redirect(url_for('admin.reminder_panel',kind=kind,batch=batch.id),303)
    return render_panel(kind,now)


def render_panel(kind,now,error=None):
    target=request.args.get('owner',type=int)
    rows,excluded=service.audience(kind,now,override=True,user_id=target)
    page=max(1,request.args.get('page',1,type=int));shown=[]
    for facts in rows[(page-1)*25:page*25]:
        owner=facts['user'];_,reason=service.candidate(owner,kind,now)
        shown.append(dict(id=owner.id,name=owner.full_name,email=masked_email(owner.email),business=facts['business'].name,
            end=local_time(facts['end']),recent=reason=='recent_contact'))
    deliveries=[]
    batch_id=request.args.get('batch',type=int)
    if batch_id:
        batch=AdminReminderBatch.query.filter_by(id=batch_id,actor_id=current_user.id).first_or_404()
        deliveries=service.batch_result(batch)
    ready,allowed=service.email_gate()
    template=service.MESSAGES[kind]
    if rows:
        variants={service.automatic_message(kind,f,now) for f in rows[:25]}
        if len(variants)==1:template=variants.pop()
    return render_template('admin/reminders.html',kind=kind,rows=shown,excluded=excluded,total=len(rows),
        template=request.form.get('message',template) if error else template,page=page,target=target,placeholders=service.PLACEHOLDERS,request_key=secrets.token_urlsafe(24),
        email_ready=ready=='ready',error=error,deliveries=deliveries,timings=service.TIMINGS,
        schedules={r.key:r for r in AdminReminderSchedule.query.all()},
        disabled_ids={r['id'] for r in shown if r['email'] and db.session.get(User,r['id']).email.lower() not in allowed})


@admin_bp.get('/reminders/deliveries/<int:delivery_id>')
@admin_required
def reminder_delivery(delivery_id):
    row=db.session.get(AdminReminderDelivery,delivery_id)
    if row is None:abort(404)
    # Signed stop/feedback links aren't needed when reviewing the message in Activity.
    body=row.body.split('\n\nStop reminders:',1)[0]
    owner=db.session.get(User,row.user_id)
    batch=db.session.get(AdminReminderBatch,row.batch_id)
    return render_template('admin/reminder_delivery.html',row=row,body=body,email=masked_email(owner.email),
        actor_name=db.session.get(User,batch.actor_id).full_name,at=local_time(row.created_at))


@admin_bp.route('/reminders/stop/<value>',methods=['GET','POST'])
def stop_reminders(value):
    try:user,_=service.token_user(value,'stop')
    except ValueError:abort(404)
    done=False
    if request.method=='POST':
        lock_owner(user.id)
        preference=db.session.get(CustomerReminderPreference,user.id)
        if not preference:preference=CustomerReminderPreference(user_id=user.id);db.session.add(preference)
        if not preference.stopped_at:
            preference.stopped_at=datetime.utcnow()
            alerts=db.session.get(AdminAlertPreference,user.id)
            if alerts:
                alerts.failed_payments=alerts.security_alerts=alerts.daily_summary=False;alerts.version+=1
            service.audit('REMINDERS_STOPPED',None,user.id,'Stopped optional reminder emails. Transactional messages are unchanged.')
            db.session.commit()
        done=True
    response=current_app.make_response(render_template('admin/reminder_public.html',mode='stop',done=done,reasons=()))
    response.headers['Cache-Control']='private, no-store';response.headers['Referrer-Policy']='same-origin'
    return response


@admin_bp.route('/reminders/feedback/<value>',methods=['GET','POST'])
def reminder_feedback(value):
    try:user,data=service.token_user(value,'feedback')
    except ValueError:abort(404)
    row=db.session.get(AdminReminderDelivery,data.get('delivery'))
    if (not row or row.user_id!=user.id or row.kind!='winback' or row.is_test
        or not row.include_feedback or row.email_status not in ('sent','unknown')):abort(404)
    done=False
    if request.method=='POST':
        reason=request.form.get('reason')
        if reason not in service.REASONS:abort(400)
        lock_owner(user.id)
        previous=db.session.get(AdminReminderFeedback,row.id)
        if previous and previous.reason!=reason:abort(409)
        if not previous:
            db.session.add(AdminReminderFeedback(delivery_id=row.id,reason=reason))
            service.audit('REMINDER_FEEDBACK_RECEIVED',None,user.id,'Shared an optional plan feedback answer.')
            db.session.commit()
        done=True
    response=current_app.make_response(render_template('admin/reminder_public.html',mode='feedback',done=done,reasons=service.REASONS))
    response.headers['Cache-Control']='private, no-store';response.headers['Referrer-Policy']='same-origin'
    return response


@admin_bp.get('/internal/reminders/dispatch')
def reminder_worker():
    secret=current_app.config.get('ADMIN_REMINDERS_DISPATCH_SECRET','')
    if not isinstance(secret,str) or len(secret)<32 or not compare_digest(request.headers.get('Authorization','').encode(),('Bearer '+secret).encode()):abort(404)
    result=service.dispatch_one()
    if result=='idle':result=service.dispatch_alert_one()
    response=jsonify(result=result);response.headers['Cache-Control']='private, no-store';return response


@admin_bp.cli.command('dispatch-reminders')
@click.option('--limit',type=click.IntRange(1,100),default=20)
def reminder_command(limit):
    """Bounded, independently approved dispatch; disabled by default."""
    outcomes={}
    for _ in range(limit):
        result=service.dispatch_one()
        if result=='idle':result=service.dispatch_alert_one()
        outcomes[result]=outcomes.get(result,0)+1
        if result not in ('sent','failed','unknown','suppressed'):break
    click.echo(outcomes)
