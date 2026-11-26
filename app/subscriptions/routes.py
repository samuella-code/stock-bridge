from app.subscriptions.entitlements import selected_business
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from datetime import datetime, timedelta
from hashlib import sha256
import json
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from io import BytesIO
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app import db
from app.models import Payment, Business, RecurringSubscription, BillingEvent, User
from app.payments.service import PaystackError
from app.subscriptions.entitlements import effective_access, account_for, PLANS, plan_spec, immediate_upgrade
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
        return render_template('subscriptions/index.html', business=business, plan_prices={plan: {interval: PLANS[(plan, interval)]['amount'] / 100 for interval in ('monthly', 'yearly')} for plan in ('basic', 'plus', 'pro')})
    access = effective_access(current_user, business=business) if current_user.is_authenticated else None
    history = RecurringSubscription.query.filter_by(user_id=current_user.id).order_by(RecurringSubscription.id.desc()).limit(20).all() if current_user.is_authenticated else []
    events = BillingEvent.query.filter(BillingEvent.user_id==current_user.id, BillingEvent.kind.notin_(('provider_event','business_created','business_selected','cancellation_feedback'))).order_by(BillingEvent.id.desc()).limit(10).all() if current_user.is_authenticated else []
    # Presentation prices come from the same authoritative configuration as checkout.
    plan_prices = {
        plan: {interval: PLANS[(plan, interval)]["amount"] / 100
               for interval in ("monthly", "yearly")}
        for plan in ("basic", "plus", "pro")
    }
    return render_template('subscriptions/billing.html', access=access,
        history=history, events=events,
        account=account_for(current_user) if current_user.is_authenticated else None,
        provider_ready=provider.configured(), plan_prices=plan_prices)


@subscriptions_bp.get('/receipt/<int:payment_id>.pdf')
@login_required
def receipt(payment_id):
    """Download a verified payment receipt belonging to the signed-in customer."""
    payment = Payment.query.filter_by(id=payment_id, status='success').first_or_404()
    if not payment.paid_at or payment.currency != 'NGN':
        abort(404)
    business = db.session.get(Business, payment.business_id) if payment.business_id else None
    if (not business or business.user_id != current_user.id
            or payment.user_id not in (None, current_user.id)):
        abort(404)
    subscription = (db.session.get(RecurringSubscription, payment.subscription_id)
                    if payment.subscription_id else None)
    if subscription and subscription.user_id != current_user.id:
        abort(404)

    paid_at = (payment.paid_at + timedelta(hours=1)).strftime('%d/%m/%Y %H:%M WAT')
    amount = f"₦{payment.amount_kobo / 100:,.2f} {payment.currency}"
    if payment.product == 'subscription':
        product = (
            f"{payment.plan_code.title()} {payment.billing_interval.title()} subscription"
            if payment.plan_code and payment.billing_interval else 'Subscription payment'
        )
    elif payment.product == 'lifetime':
        product = 'Lifetime Access'
    else:
        product = 'StockBridge payment'
    paid_until = 'Lifetime' if payment.product == 'lifetime' else 'Not available'
    if subscription:
        period_key = 'payment-period:' + sha256(payment.reference.encode()).hexdigest()
        period_event = BillingEvent.query.filter_by(
            event_key=period_key, user_id=current_user.id,
            subscription_id=subscription.id, kind='provider_event',
        ).first()
        if period_event:
            try:
                paid_period = json.loads(period_event.message)
                period_end = datetime.fromisoformat(paid_period[1])
                paid_until = (period_end + timedelta(hours=1)).strftime('%d/%m/%Y')
            except (ValueError, TypeError, IndexError):
                paid_until = 'Not available'
    styles = getSampleStyleSheet()
    story = [
        Paragraph("StockBridge", styles['Title']),
        Paragraph("Payment receipt", styles['Heading2']),
        Spacer(1, 14),
        Paragraph(f"Customer: {escape(current_user.full_name or 'StockBridge customer')}", styles['BodyText']),
        Paragraph(f"Business: {escape(business.name)}", styles['BodyText']),
        Spacer(1, 12),
    ]
    rows = [
        ['Description', product],
        ['Amount paid', amount],
        ['Paid on', paid_at],
        ['Paid through', paid_until],
        ['Payment reference', payment.reference],
        ['Payment status', 'Paid'],
    ]
    table = Table(rows, colWidths=[125, 340], hAlign='LEFT')
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F8FAF8')),
        ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#60756B')),
        ('TEXTCOLOR', (1, 0), (1, -1), colors.HexColor('#17352B')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E2EBE5')),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('PADDING', (0, 0), (-1, -1), 9),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    story.extend([table, Spacer(1, 18),
        Paragraph("Thank you for using StockBridge. Know your stock. Understand your profit.",
                  styles['BodyText'])])
    output = BytesIO()
    SimpleDocTemplate(output, pagesize=letter, title="StockBridge payment receipt").build(story)
    output.seek(0)
    safe_reference = ''.join(ch for ch in payment.reference if ch.isalnum() or ch in '-_')[:60]
    return send_file(output, mimetype='application/pdf', as_attachment=True,
                     download_name=f"stockbridge-receipt-{safe_reference}.pdf")


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
        if access.legacy and plan not in ('plus', 'pro'):
            raise ValueError('Your existing account access already includes Basic tools. Plus or Pro is optional.')
        sub = access.subscription
        if sub and (sub.plan_code, sub.billing_interval) == (plan, interval):
            raise ValueError('That plan and interval are already selected.')
        immediate = not sub or (immediate_upgrade(sub.plan_code, plan, sub.billing_interval, interval))
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


@subscriptions_bp.route('/trial-reminders/stop/<token>', methods=['GET', 'POST'])
def stop_trial_reminders(token):
    """Confirm a signed trial-reminder opt-out without mutating state on GET."""
    serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    try:
        payload = serializer.loads(token, salt='stop-trial-reminders', max_age=30 * 86400)
    except (BadSignature, SignatureExpired):
        abort(404)
    if not isinstance(payload, dict) or not isinstance(payload.get('user_id'), int):
        abort(404)
    user = db.session.get(User, payload['user_id'])
    if not user or user.email != payload.get('email'):
        abort(404)
    if request.method == 'POST':
        if request.form.get('confirm') != 'yes':
            flash('Confirm that you want to stop trial reminder emails.', 'warning')
            return redirect(url_for('subscriptions.stop_trial_reminders', token=token))
        user.trial_reminders_enabled = False
        db.session.commit()
        return render_template('subscriptions/stop_trial_reminders.html',
            token=token, already_stopped=True, updated=True)
    return render_template('subscriptions/stop_trial_reminders.html',
        token=token, already_stopped=not user.trial_reminders_enabled, updated=False)


@subscriptions_bp.route('/<int:subscription_id>/renewal/restore', methods=['GET', 'POST'])
@login_required
def restore_renewal(subscription_id):
    enabled()
    sub = owned(subscription_id)
    if request.method == 'GET':
        if (not sub.cancel_at_period_end or not sub.current_period_end
                or sub.current_period_end <= datetime.utcnow()):
            flash('This subscription has no renewable paid period. Choose a plan to restart access.', 'warning')
            return redirect(url_for('subscriptions.index'))
        return render_template('subscriptions/restore_renewal.html', subscription=sub,
            paid_until=(sub.current_period_end + timedelta(hours=1)).strftime('%d/%m/%Y'),
            amount=f"₦{sub.amount_kobo / 100:,.2f}")
    if request.form.get('confirm') != 'yes':
        flash('Confirm that you want to turn renewal back on.', 'warning')
        return redirect(url_for('subscriptions.index'))
    try:
        billing.enable(current_user, sub)
        flash('Your subscription renewal is back on.', 'success')
        return redirect(url_for('subscriptions.index'))
    except (ValueError, PaystackError) as error:
        return failed(error)


@subscriptions_bp.route('/<int:subscription_id>/feedback', methods=['GET', 'POST'])
@login_required
def cancellation_feedback(subscription_id):
    enabled()
    sub = owned(subscription_id)
    if not (sub.cancel_at_period_end or sub.status in ('cancelled', 'cancel_at_period_end', 'non_renewing')):
        abort(404)
    reasons = (
        'Too expensive', 'Missing a feature I need', 'Not using StockBridge enough',
        'Switching to another tool', 'Technical issues', 'Other',
    )
    if request.method == 'POST':
        reason = request.form.get('reason', '')
        if reason not in reasons:
            abort(400)
        details = request.form.get('details', '').strip()[:300]
        message = f"Reason: {reason}" + (f"\\nDetails: {details}" if details else '')
        key = f'cancellation-feedback:{sub.id}'
        event = BillingEvent.query.filter_by(event_key=key, user_id=current_user.id).first()
        if event is None:
            event = BillingEvent(event_key=key, user_id=current_user.id,
                subscription_id=sub.id, kind='cancellation_feedback', message=message)
            db.session.add(event)
        else:
            event.message = message
        db.session.commit()
        flash('Thank you. Your feedback helps us improve StockBridge.', 'success')
        return redirect(url_for('subscriptions.index'))
    return render_template('subscriptions/cancellation_feedback.html',
        subscription=sub, reasons=reasons)


@subscriptions_bp.post('/<int:subscription_id>/card')
@login_required
def card(subscription_id):
    enabled()
    sub=owned(subscription_id)
    try:
        return redirect(billing.card_update_link(current_user,sub),code=303)
    except (ValueError,PaystackError) as error:
        return failed(error)
