from app.subscriptions.entitlements import selected_business
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from io import BytesIO
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app import db
from app.models import Payment, Business, RecurringSubscription, BillingEvent
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
    events = BillingEvent.query.filter(BillingEvent.user_id==current_user.id, BillingEvent.kind.notin_(('provider_event','business_created','business_selected'))).order_by(BillingEvent.id.desc()).limit(10).all() if current_user.is_authenticated else []
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
    payment = Payment.query.filter_by(
        id=payment_id, user_id=current_user.id, status='success'
    ).first_or_404()
    if not payment.paid_at or payment.currency != 'NGN':
        abort(404)
    business = db.session.get(Business, payment.business_id) if payment.business_id else None
    if not business or business.user_id != current_user.id:
        abort(404)
    subscription = (db.session.get(RecurringSubscription, payment.subscription_id)
                    if payment.subscription_id else None)
    if subscription and subscription.user_id != current_user.id:
        abort(404)

    paid_at = (payment.paid_at + __import__('datetime').timedelta(hours=1)).strftime('%d/%m/%Y %H:%M WAT')
    amount = f"₦{payment.amount_kobo / 100:,.2f} {payment.currency}"
    product = (
        f"{subscription.plan_code.title()} {subscription.billing_interval.title()} subscription"
        if subscription else 'Lifetime Access'
    )
    paid_until = (
        (subscription.current_period_end + __import__('datetime').timedelta(hours=1)).strftime('%d/%m/%Y')
        if subscription and subscription.current_period_end else
        ('Lifetime' if not subscription else 'Not available')
    )
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


@subscriptions_bp.post('/<int:subscription_id>/card')
@login_required
def card(subscription_id):
    enabled()
    sub=owned(subscription_id)
    try:
        return redirect(billing.card_update_link(current_user,sub),code=303)
    except (ValueError,PaystackError) as error:
        return failed(error)
