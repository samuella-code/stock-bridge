"""Explicit billing-notification worker; never scheduled by application startup or deployment."""
from datetime import datetime, timedelta
from hashlib import sha256
import click
from flask import current_app
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy import update
from app import db
from app.models import AccountBilling, BillingEvent, Payment, RecurringSubscription, StaffMembership, User
from app.subscriptions.billing import lock_account, notice
from app.subscriptions.entitlements import effective_access, account_for, PLANS
from app.email_service import _transactional


EMAIL_EVENT_KINDS = {
    'trial_ending', 'trial_expired', 'subscription_expired',
    'renewal_failed', 'renewal_reminder', 'subscription_paid',
    'cancellation_scheduled', 'plan_change_scheduled',
}


def _eligible_customer(user):
    if not (user and user.role == 'user' and not user.admin_enabled
            and not user.staff_only and user.email_verified_at and not user.suspended_at):
        return False
    # Staff accounts are not subscription customers, even when their account
    # row is otherwise indistinguishable from a normal customer account.
    return StaffMembership.query.filter_by(user_id=user.id, state='active').first() is None


def trial_reminder_stop_token(user):
    """Signed opt-out token; the confirmation POST, not the email-link GET, changes state."""
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY']).dumps(
        {'user_id': user.id, 'email_digest': sha256(user.email.lower().encode()).hexdigest()}, salt='stop-trial-reminders')


def _wat(value):
    return (value + timedelta(hours=1)).strftime('%d/%m/%Y')


def queue_due(now=None):
    """Queue day-five/trial-ended and three-day renewal notices idempotently."""
    now = now or datetime.utcnow()
    for account in AccountBilling.query.filter(AccountBilling.trial_ends_at.isnot(None)).yield_per(100):
        user = db.session.get(User, account.user_id)
        if not _eligible_customer(user) or not user.trial_reminders_enabled:
            continue
        lock_account(user.id)
        access = effective_access(user, now=now)
        if access.kind == 'trial' and now >= account.trial_ends_at - timedelta(days=2):
            notice(user.id, f'trial-ending:{user.id}', 'trial_ending',
                   'Your 7-day StockBridge trial ends in 2 days. Choose a plan to keep recording business activity. No automatic charge follows your trial.')
        elif now >= account.trial_ends_at and access.kind == 'restricted':
            notice(user.id, f'trial-expired:{user.id}', 'trial_expired',
                   'Your free trial has ended. Your records remain safe and available to view. Choose a plan to record new activity.')

    renewal_window = now + timedelta(days=3)
    due_subscriptions = RecurringSubscription.query.filter(
        RecurringSubscription.current_period_end > now,
        RecurringSubscription.current_period_end <= renewal_window,
        RecurringSubscription.cancel_at_period_end.is_(False),
        RecurringSubscription.status == 'active',
    ).yield_per(100)
    for sub in due_subscriptions:
        user = db.session.get(User, sub.user_id)
        if not _eligible_customer(user):
            continue
        lock_account(user.id)
        access = effective_access(user, now=now)
        if not access.subscription or access.subscription.id != sub.id:
            continue
        period_end = sub.current_period_end.isoformat()
        notice(user.id, f'renewal-reminder:{sub.id}:{period_end}', 'renewal_reminder',
               f'Your {sub.plan_code.title()} {sub.billing_interval} plan is due to renew on {_wat(sub.current_period_end)}. Amount: ₦{sub.amount_kobo / 100:,.2f}.',
               sub.id)

    for sub in RecurringSubscription.query.filter(
            RecurringSubscription.current_period_end <= now).yield_per(100):
        user = db.session.get(User, sub.user_id)
        if not _eligible_customer(user):
            continue
        lock_account(user.id)
        access = effective_access(user, now=now)
        if access.subscription and access.subscription.id == sub.id:
            continue
        fallback = ('Your existing account access continues.' if access.legacy
                    else 'Your records remain safe and available to view.')
        notice(user.id, f'period-ended:{sub.id}:{sub.current_period_end.isoformat()}',
               'subscription_expired', 'This paid subscription period has ended. ' + fallback, sub.id)
    db.session.commit()


def _billing_email(event, user, subscription):
    """Render event-specific billing content while retaining a plain-text body."""
    host = current_app.config['CUSTOMER_HOST']
    link = 'https://' + host + current_app.url_map.bind(host).build('subscriptions.index')
    kind = event.kind
    details = {}
    message = event.message
    feedback_url = None
    stop_reminders_url = None
    stop_reminders_label = None
    if subscription:
        details.update(
            plan=subscription.plan_code.title(),
            interval=subscription.billing_interval,
            amount=f"₦{subscription.amount_kobo / 100:,.2f}",
        )
    if kind in ('trial_ending', 'trial_expired'):
        token = trial_reminder_stop_token(user)
        stop_path = current_app.url_map.bind(host).build(
            'subscriptions.stop_trial_reminders', {'token': token})
        stop_reminders_url = 'https://' + host + stop_path
        stop_reminders_label = 'Stop trial reminders'
    if kind == 'trial_ending':
        title, label = 'Your trial ends in 2 days', 'Choose a plan'
        status = 'TRIAL ENDING'
        plan_prices = {
            plan: {interval: f"₦{PLANS[(plan, interval)]['amount'] / 100:,.0f}"
                   for interval in ('monthly', 'yearly')}
            for plan in ('basic', 'plus', 'pro')
        }
        details['plan_prices'] = plan_prices
    elif kind == 'trial_expired':
        title, label, status = 'Your free trial has ended', 'Choose a plan', 'TRIAL ENDED'
    elif kind == 'renewal_failed':
        title, label, status = 'We could not renew your plan', 'Update payment method', 'PAYMENT ISSUE'
    elif kind == 'renewal_reminder':
        title, label, status = 'Your plan renews in 3 days', 'View billing', 'RENEWAL REMINDER'
        if subscription:
            details['renewal_date'] = _wat(subscription.current_period_end)
    elif kind == 'subscription_paid':
        title, label, status = 'Thank you. Payment received.', 'View billing', 'PAYMENT RECEIVED'
        paid_until = event.message.partition('Paid access until: ')[2].strip().rstrip('.')
        details['paid_until'] = paid_until
    elif kind == 'subscription_expired':
        title, label, status = 'Your paid plan has ended', 'Choose a plan', 'PLAN ENDED'
    elif kind == 'cancellation_scheduled':
        title, label, status = 'Your renewal has been stopped', 'View billing', 'RENEWAL OFF'
        if subscription and subscription.current_period_end:
            paid_until = _wat(subscription.current_period_end)
            title = f'Your plan ends on {paid_until}'
            details['paid_until'] = paid_until
            account = account_for(user)
            if account and account.legacy_granted_at:
                message = (f"Renewal is off. Your paid subscription ends on {paid_until}. "
                           "Your existing account access continues, and your records remain safe.")
            else:
                message = (f"Renewal is off. You can keep recording business activity until {paid_until}. "
                           "After that, you will not be able to add new records on this plan. "
                           "Your records remain safe and available to view.")
            if subscription.cancel_at_period_end:
                restore_path = current_app.url_map.bind(host).build(
                    'subscriptions.restore_renewal', {'subscription_id': subscription.id})
                link = 'https://' + host + restore_path
                label = 'Turn renewal back on'
            feedback_path = current_app.url_map.bind(host).build(
                'subscriptions.cancellation_feedback', {'subscription_id': subscription.id})
            feedback_url = 'https://' + host + feedback_path
        else:
            title, label, status = 'Your scheduled plan was cancelled', 'View billing', 'PLAN CANCELLED'
            message = ('Your scheduled subscription was cancelled before it began. '
                       'No renewal will be charged. Your current account access and records remain unchanged.')
            feedback_url = None
    elif kind == 'plan_change_scheduled':
        title, label, status = 'Your plan change is scheduled', 'View billing', 'PLAN UPDATE'
    else:
        title, label, status = 'Your StockBridge account', 'View billing', 'ACCOUNT UPDATE'
    receipt_url = None
    if kind == 'subscription_paid':
        reference = event.event_key.partition('payment:')[2]
        payment = Payment.query.filter_by(
            reference=reference, user_id=user.id, subscription_id=event.subscription_id,
            status='success',
        ).first() if reference else None
        if payment and payment.paid_at:
            receipt_path = current_app.url_map.bind(host).build(
                'subscriptions.receipt', {'payment_id': payment.id})
            receipt_url = 'https://' + host + receipt_path
            paid_on = event.message.partition('Paid on: ')[2].partition('. Paid access until:')[0].strip()
            details['paid_on'] = paid_on
            details['reference'] = payment.reference[:8] + '…'
            message += f"\\nReference: {details['reference']}\\n\\nDownload receipt PDF: {receipt_url}"
    return _transactional(
        'billing_notice', title, user.email, user.full_name, message,
        preheader=event.message[:90], event_kind=kind, status=status,
        headline=title, message=message, link=link, action_label=label,
        receipt_url=receipt_url, feedback_url=feedback_url,
        stop_reminders_url=stop_reminders_url, stop_reminders_label=stop_reminders_label, **details,
    )


def deliver(limit=100, now=None):
    """Deliver eligible notices; optional clock keeps lifecycle checks deterministic."""
    now = now or datetime.utcnow()
    ids = [row[0] for row in db.session.query(BillingEvent.id)
           .filter(BillingEvent.kind.in_(EMAIL_EVENT_KINDS),
                   BillingEvent.email_claimed_at.is_(None))
           .order_by(BillingEvent.id).limit(limit)]
    sent = 0
    for event_id in ids:
        claimed = db.session.execute(update(BillingEvent).where(
            BillingEvent.id == event_id,
            BillingEvent.email_claimed_at.is_(None)
        ).values(email_claimed_at=now))
        db.session.commit()
        if not claimed.rowcount:
            continue
        event = db.session.get(BillingEvent, event_id)
        user = db.session.get(User, event.user_id) if event else None
        if not _eligible_customer(user):
            continue
        if event.kind in ('trial_ending', 'trial_expired') and not user.trial_reminders_enabled:
            continue
        subscription = (db.session.get(RecurringSubscription, event.subscription_id)
                        if event.subscription_id else None)
        # Do not send lifecycle messages that became stale after they were queued.
        if event.kind == 'trial_ending' and effective_access(user, now=now).kind != 'trial':
            continue
        if event.kind == 'trial_expired' and effective_access(user, now=now).kind != 'restricted':
            continue
        if event.kind == 'renewal_reminder' and (
                not subscription or subscription.status != 'active'
                or subscription.cancel_at_period_end
                or event.event_key != f'renewal-reminder:{subscription.id}:{subscription.current_period_end.isoformat()}'):
            continue
        if event.kind == 'renewal_failed' and (not subscription or subscription.status != 'past_due'):
            continue
        if event.kind == 'cancellation_scheduled' and subscription and not subscription.cancel_at_period_end:
            continue
        try:
            delivered = _billing_email(event, user, subscription)
        except Exception:
            current_app.logger.warning(
                "Billing email delivery needs review for event %s.", event.id)
            delivered = False
        if delivered:
            event.email_sent_at = datetime.utcnow()
            db.session.commit()
            sent += 1
    return sent


def register_commands(app):
    @app.cli.command('billing-preflight')
    def preflight_command():
        from app.subscriptions.legacy import preflight
        _, report = preflight(db.session.connection())
        for key, value in report.items():
            click.echo(f'{key}: {value}')
        db.session.rollback()
        if report['multiple_lifetime_businesses'] or report['active_lifetime_without_payment_evidence']:
            raise click.ClickException('STOP: lifetime evidence needs review; no changes made.')
        click.echo('READ ONLY: no records changed.')

    @app.cli.command('billing-notifications')
    def notifications_command():
        if not app.config.get('SUBSCRIPTIONS_ENABLED'):
            raise click.ClickException('Subscription rollout is disabled.')
        queue_due()
        click.echo(f'Billing notifications sent: {deliver()}')
