from app.subscriptions.entitlements import selected_business
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from app import db
from app.models import RecurringSubscription, BillingEvent
from app.payments.service import PaystackError
from app.subscriptions.entitlements import effective_access, account_for, PLANS, plan_spec
from app.subscriptions import billing, provider

subscriptions_bp = Blueprint('subscriptions', __name__, url_prefix='/plans')


def enabled():
    if not current_app.config.get('SUBSCRIPTIONS_ENABLED'):
        abort(404)


def owned(subscription_id):
    return RecurringSubscription.query.filter_by(id=subscription_id, user_id=current_user.id).first_or_404()


def failed(error):
    db.session.rollback()
    # Provider errors can contain operational details; keep them out of customer responses.
    flash(str(error) if isinstance(error, ValueError) else 'Secure billing could not complete. Check your billing status or contact support before retrying.', 'warning')
    return redirect(url_for('subscriptions.index'))


@subscriptions_bp.get('/')
def index():
    business = selected_business(current_user) if current_user.is_authenticated else None
    if not current_app.config.get('SUBSCRIPTIONS_ENABLED'):
        return render_template('subscriptions/index.html', business=business, lifetime_price=current_app.config['LIFETIME_PRICE_NAIRA'])
    access = effective_access(current_user, business=business) if current_user.is_authenticated else None
    history = RecurringSubscription.query.filter_by(user_id=current_user.id).order_by(RecurringSubscription.id.desc()).limit(20).all() if current_user.is_authenticated else []
    events = BillingEvent.query.filter(BillingEvent.user_id==current_user.id, BillingEvent.kind.notin_(('provider_event','business_created','business_selected'))).order_by(BillingEvent.id.desc()).limit(10).all() if current_user.is_authenticated else []
    # Presentation prices come from the same authoritative configuration as checkout.
    plan_prices = {
        plan: {interval: PLANS[(plan, interval)]["amount"] / 100
               for interval in ("monthly", "yearly")}
        for plan in ("basic", "plus")
    }
    return render_template('subscriptions/billing.html', access=access,
        history=history, events=events,
        account=account_for(current_user) if current_user.is_authenticated else None,
        provider_ready=provider.configured(), plan_prices=plan_prices)


@subscriptions_bp.get('/<int:subscription_id>/change')
@login_required
def review_change(subscription_id):
    enabled()
    sub = owned(subscription_id)
    current = effective_access(current_user).subscription
    if not current or current.id != sub.id:
        return failed(ValueError('Only your currently paid subscription can be changed.'))
    return review()


@subscriptions_bp.get('/review')
@login_required
def review():
    enabled()
    plan, interval = request.args.get('plan', ''), request.args.get('interval', '')
    try:
        spec = plan_spec(plan, interval)
        access = effective_access(current_user)
        if access.legacy and plan != 'plus':
            raise ValueError('Your Lifetime Access already includes Basic tools. Plus is optional.')
        sub = access.subscription
        if sub and (sub.plan_code, sub.billing_interval) == (plan, interval):
            raise ValueError('That plan and interval are already selected.')
        immediate = not sub or (sub.plan_code == 'basic' and plan == 'plus' and sub.billing_interval == interval)
        return render_template('subscriptions/review.html', plan=plan, interval=interval,
            spec=spec, sub=sub, immediate=immediate, provider_ready=provider.configured(), access=access)
    except ValueError as error:
        return failed(error)


@subscriptions_bp.post('/checkout')
@login_required
def checkout():
    enabled()
    try:
        link = billing.begin_checkout(current_user, request.form.get('plan',''), request.form.get('interval',''))
        return redirect(link, code=303)
    except (ValueError, PaystackError) as error:
        return failed(error)


@subscriptions_bp.get('/callback')
@login_required
def callback():
    enabled()
    try:
        billing.verify_checkout(current_user, request.args.get('reference',''))
        flash('Subscription payment verified. Your business tools are available.', 'success')
        return redirect(url_for('subscriptions.index'))
    except (ValueError, PaystackError) as error:
        return failed(error)


@subscriptions_bp.post('/<int:subscription_id>/verify')
@login_required
def verify(subscription_id):
    enabled()
    sub = owned(subscription_id)
    if not sub.checkout_reference:
        flash('This scheduled subscription will update after its provider invoice arrives.', 'warning')
        return redirect(url_for('subscriptions.index'))
    try:
        billing.verify_checkout(current_user, sub.checkout_reference)
        flash('Billing status checked.', 'success')
        return redirect(url_for('subscriptions.index'))
    except (ValueError, PaystackError) as error:
        return failed(error)


@subscriptions_bp.post('/<int:subscription_id>/cancel')
@login_required
def cancel(subscription_id):
    enabled()
    sub = owned(subscription_id)
    if request.form.get('confirm') != 'yes':
        flash('Confirm that you want to stop renewal.', 'warning')
        return redirect(url_for('subscriptions.index'))
    try:
        billing.cancel(current_user, sub)
        flash('Renewal stopped. Already-paid access remains until the period ends.', 'success')
        return redirect(url_for('subscriptions.index'))
    except (ValueError, PaystackError) as error:
        return failed(error)


@subscriptions_bp.post('/<int:subscription_id>/change')
@login_required
def change(subscription_id):
    enabled()
    sub = owned(subscription_id)
    if request.form.get('confirm') != 'yes':
        flash('Confirm the replacement plan and billing terms.', 'warning')
        return redirect(url_for('subscriptions.index'))
    try:
        link = billing.change_plan(current_user, sub, request.form.get('plan',''), request.form.get('interval',''))
        if link:
            return redirect(link, code=303)
        flash('Plan change scheduled for the end of your paid period.', 'success')
        return redirect(url_for('subscriptions.index'))
    except (ValueError, PaystackError) as error:
        return failed(error)


@subscriptions_bp.post('/<int:subscription_id>/card')
@login_required
def card(subscription_id):
    enabled()
    sub=owned(subscription_id)
    try:
        return redirect(billing.card_update_link(current_user,sub),code=303)
    except (ValueError,PaystackError) as error:
        return failed(error)
