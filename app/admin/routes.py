from app.error_handling import log_failure
import hashlib
import secrets
import time
from datetime import date, datetime, time as dt_time, timedelta
from functools import wraps
from flask import Blueprint, abort, current_app, flash, has_request_context, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_user, logout_user
from sqlalchemy import and_, func, or_, update
from sqlalchemy.orm import joinedload, selectinload
from werkzeug.security import generate_password_hash, check_password_hash
from app import db
from app.models import (
    AccountBilling, AdminAuthenticator, AdminLoginAttempt, AdminPasswordReset, AuditLog,
    Business, Expense, Payment, Product, RecurringSubscription,
    Restock, Sale, User,
)
from app.email_service import _send_email, safely_send, send_account_status_email, send_access_receipt_once
from app.admin.security import valid_admin_password
from app.admin import mfa
from app.admin.dashboard import (ISSUE_STATUSES, PERIODS, business_access_counts, dashboard, owner_alert_query, owner_query)
from cryptography.fernet import InvalidToken
from app.payments.service import PaystackError, verify_transaction

admin_bp=Blueprint("admin",__name__,url_prefix="/admin")
admin_api_bp=Blueprint("admin_api",__name__,url_prefix="/api/admin")
_DUMMY_HASH=generate_password_hash("not-a-real-admin-password")

def admin_required(view):
    @wraps(view)
    def wrapped(*args,**kwargs):
        if not current_user.is_authenticated:
            if request.blueprint=="admin_api":
                return jsonify(error="Admin authentication required"),401
            return redirect(url_for("admin.login"))
        db.session.refresh(current_user)
        if not (current_user.role=="admin" or current_user.admin_enabled) or not session.get("admin_session") or current_user.suspended_at:
            abort(403)
        last=session.get("admin_last_activity",0)
        if (time.time()-last>current_app.config["ADMIN_IDLE_TIMEOUT_SECONDS"]
                or session.get("admin_auth_version")!=current_user.admin_auth_version
                or not _admin_second_factor_satisfied(current_user)):
            logout_user()
            session.clear()
            if request.blueprint=="admin_api":
                return jsonify(error="Admin session expired"),401
            flash("Your admin session expired. Please sign in again.","warning")
            return redirect(url_for("admin.login"))
        session["admin_last_activity"]=time.time()
        return view(*args,**kwargs)
    return wrapped

def log(action,description,actor=None,business_id=None,target_type=None,target_id=None):
    if actor:
        actor.last_activity_at=datetime.utcnow()
    db.session.add(AuditLog(action=action,description=description[:300],
        actor_id=actor.id if actor else None,business_id=business_id,
        target_type=target_type,target_id=target_id,
        ip_address=request.remote_addr if has_request_context() else None))

def _login_key(email):
    # The server-observed address limits distributed guesses against an account.
    raw=(email+"|"+(request.remote_addr or "")).encode()
    return hashlib.sha256(raw).hexdigest()

@admin_bp.route("/login",methods=["GET","POST"])
def login():
    if current_user.is_authenticated:
        if (current_user.role=="admin" or current_user.admin_enabled) and session.get("admin_session"):
            if (time.time()-session.get("admin_last_activity",0)<=current_app.config["ADMIN_IDLE_TIMEOUT_SECONDS"]
                    and session.get("admin_auth_version")==current_user.admin_auth_version
                    and _admin_second_factor_satisfied(current_user) and not current_user.suspended_at):
                return redirect(url_for("admin.index"))
            logout_user()
            session.clear()
        elif current_user.role=="admin":
            abort(403)
    if request.method=="POST":
        email=request.form.get("email","").strip().lower()
        password=request.form.get("password","")
        key=_login_key(email)
        cutoff=datetime.utcnow()-timedelta(minutes=15)
        count=AdminLoginAttempt.query.filter(AdminLoginAttempt.identifier==key,
            AdminLoginAttempt.attempted_at>=cutoff).count()
        if count>=5:
            return render_template("admin/login.html",error="Too many attempts. Try again in 15 minutes."),429
        user=User.query.filter(User.email==email,or_(User.role=="admin",User.admin_enabled.is_(True))).first()
        valid=user.check_password(password) if user else check_password_hash(_DUMMY_HASH,password)
        if not valid or not user or user.suspended_at:
            db.session.add(AdminLoginAttempt(identifier=key))
            log("ADMIN_LOGIN_FAILED","Invalid admin credentials.")
            db.session.commit()
            return render_template("admin/login.html",error="Invalid email or password."),401
        AdminLoginAttempt.query.filter_by(identifier=key).delete()
        if _admin_mfa_required(user):
            return _start_admin_challenge(user)
        _finish_admin_login(user)
        return redirect(url_for("admin.index"))
    return render_template("admin/login.html")


def _admin_mfa_required(user):
    pilot_emails = current_app.config["ADMIN_MFA_PILOT_EMAIL"].split(",")
    return current_app.config["ADMIN_MFA_REQUIRED"] or bool(
        user.email.lower() in {email.strip() for email in pilot_emails if email.strip()}
    )


def _admin_second_factor_satisfied(user):
    return not _admin_mfa_required(user) or session.get("admin_mfa_verified") is True


def _finish_admin_login(user, *, verified=False):
    auth_version = user.admin_auth_version
    log("ADMIN_LOGIN_SUCCESS", "Administrator signed in.", actor=user)
    db.session.commit()
    logout_user()
    session.clear()
    login_user(user, fresh=True)
    session["admin_session"] = True
    session["admin_last_activity"] = time.time()
    session["admin_auth_version"] = auth_version
    if verified:
        session["admin_mfa_verified"] = True


def _admin_mfa_cipher():
    try:
        return mfa.secret_cipher(current_app.config["ADMIN_MFA_ENCRYPTION_KEY"])
    except (ValueError, TypeError):
        abort(503, description="Admin verification is unavailable.")


def _locked_admin(user_id):
    return User.query.filter_by(id=user_id).populate_existing().with_for_update().first()


def _start_admin_challenge(user):
    cipher = _admin_mfa_cipher()
    auth_version = user.admin_auth_version
    password_hash = user.password_hash
    user = _locked_admin(user.id)
    if not _admin_user(user) or user.admin_auth_version != auth_version or user.password_hash != password_hash:
        db.session.rollback()
        return render_template("admin/login.html", error="Invalid email or password."), 401
    factor = db.session.get(AdminAuthenticator, user.id)
    if factor is None:
        factor = AdminAuthenticator(user_id=user.id, encrypted_secret=cipher.encrypt(mfa.new_secret().encode("ascii")))
        db.session.add(factor)
    elif factor.enabled_at is None:
        factor.encrypted_secret = cipher.encrypt(mfa.new_secret().encode("ascii"))
    challenge = secrets.token_urlsafe(32)
    factor.challenge_digest = mfa.digest_token(challenge)
    factor.challenge_expires_at = datetime.utcnow() + timedelta(seconds=mfa.CHALLENGE_SECONDS)
    db.session.commit()
    logout_user()
    session.clear()
    session["admin_mfa_pending"] = [user.id, auth_version, challenge]
    return redirect(url_for("admin.verify_authenticator"))


@admin_bp.route("/verify-authenticator", methods=["GET", "POST"])
def verify_authenticator():
    pending = session.get("admin_mfa_pending")
    if not pending:
        return redirect(url_for("admin.login"))
    user_id, auth_version, challenge = pending
    digest = mfa.digest_token(challenge)
    now = datetime.utcnow()
    # Serialize attempts across browsers and IPs against the same account.
    user = _locked_admin(user_id) if request.method == "POST" else db.session.get(User, user_id)
    factor = db.session.get(AdminAuthenticator, user_id)
    if (not _admin_user(user) or not _admin_mfa_required(user)
            or user.admin_auth_version != auth_version or not factor
            or not factor.challenge_digest or not secrets.compare_digest(factor.challenge_digest, digest)
            or not factor.challenge_expires_at
            or factor.challenge_expires_at <= now):
        session.pop("admin_mfa_pending", None)
        return redirect(url_for("admin.login"))

    enrolling = factor.enabled_at is None
    recovery = not enrolling and (request.form if request.method == "POST" else request.args).get("method") == "recovery"
    try:
        secret = _admin_mfa_cipher().decrypt(factor.encrypted_secret).decode("ascii")
    except InvalidToken:
        abort(503, description="Admin verification is unavailable.")
    attempt_key = mfa.digest_token(f"admin-mfa:{user_id}")
    attempts = AdminLoginAttempt.query.filter(
        AdminLoginAttempt.identifier == attempt_key,
        AdminLoginAttempt.attempted_at >= now - timedelta(minutes=15),
    )

    def render_step(error=None, status=200):
        return render_template("admin/verify_authenticator.html", enrolling=enrolling, recovery=recovery,
            setup_key=secret if enrolling else None,
            setup_qr=mfa.qr_image(mfa.setup_uri(user.email, secret)) if enrolling else None,
            error=error), status

    if request.method == "GET":
        return render_step()
    if attempts.count() >= 5:
        return render_step("Too many attempts. Try again in 15 minutes.", 429)

    code = request.form.get("code", "").strip()
    step = None if recovery else mfa.matching_step(secret, code, factor.last_step)
    recovery_digest = mfa.digest_token(code.lower()) if recovery else None
    valid_recovery = recovery and recovery_digest in factor.recovery_digests
    if step is None and not valid_recovery:
        db.session.add(AdminLoginAttempt(identifier=attempt_key))
        log("ADMIN_MFA_FAILED", "Administrator verification failed.", target_type="user", target_id=user_id)
        db.session.commit()
        return render_step("That code is invalid or has already been used.", 401)

    changes = {"challenge_digest": None, "challenge_expires_at": None}
    codes = None
    if enrolling:
        codes = mfa.new_recovery_codes()
        changes.update(enabled_at=now, recovery_digests=[mfa.digest_token(code) for code in codes])
    if recovery:
        changes["recovery_digests"] = [value for value in factor.recovery_digests if value != recovery_digest]
    else:
        changes["last_step"] = step
    consumed = AdminAuthenticator.query.filter(
        AdminAuthenticator.user_id == user_id, AdminAuthenticator.challenge_digest == digest,
        AdminAuthenticator.challenge_expires_at > now,
    ).update(changes)
    if consumed != 1:
        db.session.rollback()
        session.pop("admin_mfa_pending", None)
        return redirect(url_for("admin.login"))
    attempts.delete()
    if enrolling:
        log("ADMIN_MFA_ENROLLED", "Administrator set up an authenticator.", actor=user)
    elif recovery:
        log("ADMIN_MFA_RECOVERY_USED", "Administrator signed in with a recovery code.", actor=user)
    _finish_admin_login(user, verified=True)
    if codes is not None:
        return render_template("admin/verify_authenticator.html", recovery_codes=codes)
    return redirect(url_for("admin.index"))


@admin_bp.post("/logout")
@admin_required
def logout():
    log("ADMIN_LOGOUT","Administrator signed out.",actor=current_user)
    db.session.commit()
    logout_user()
    session.clear()
    return redirect(url_for("admin.login"))

@admin_bp.post("/switch-to-business")
@admin_required
def switch_to_business():
    if current_user.role!="user" or not current_user.businesses:
        abort(403)
    log("ADMIN_SWITCH_TO_BUSINESS","Administrator opened their business workspace.",actor=current_user)
    db.session.commit()
    if request.host.split(":",1)[0].lower()==current_app.config["ADMIN_HOST"]:
        logout_user()
        session.clear()
        return redirect(f"https://{current_app.config['CUSTOMER_HOST']}/auth/login")
    session.pop("admin_session",None)
    session.pop("admin_last_activity",None)
    session.pop("admin_auth_version",None)
    session.pop("admin_mfa_verified",None)
    return redirect(url_for("main.dashboard"))

def _admin_user(user):
    return user and (user.role=="admin" or user.admin_enabled) and not user.suspended_at


def customer_query():
    return User.query.filter(User.role=="user", User.admin_enabled.is_(False))


def customer_business_query():
    return Business.query.join(User).filter(User.role=="user", User.admin_enabled.is_(False))


def verified_payments_query():
    # A legacy 'success' value alone is insufficient evidence of granted access.
    return Payment.query.join(Business, Payment.business_id==Business.id).join(
        User, Business.user_id==User.id).filter(
        User.role=="user", User.admin_enabled.is_(False),
        Payment.status=="success", Payment.product=="lifetime",
        Payment.provider=="paystack", Payment.currency=="NGN",
        Payment.amount_kobo==current_app.config["LIFETIME_PRICE_NAIRA"]*100,
        Payment.paid_at.isnot(None), func.lower(Payment.customer_email)==func.lower(User.email))


def payment_issues_query():
    return Payment.query.filter(Payment.product=="lifetime").filter(or_(
        and_(Payment.status.in_(("initialized","pending")),
             Payment.created_at<datetime.utcnow()-timedelta(days=2)),
        and_(Payment.status=="success", Payment.business_id.is_(None)),
        Payment.amount_kobo!=current_app.config["LIFETIME_PRICE_NAIRA"]*100,
        Payment.currency!="NGN", Payment.status=="failed"))


def report_period():
    period=request.args.get("period","30d")
    now=datetime.utcnow()
    today=datetime.combine(now.date(),dt_time.min)
    if period=="today": start=today
    elif period=="7d": start=today-timedelta(days=6)
    elif period=="30d": start=today-timedelta(days=29)
    elif period=="custom":
        try:
            first=date.fromisoformat(request.args.get("start",""))
            last=date.fromisoformat(request.args.get("end",""))
            if first>last or (last-first).days>366: raise ValueError
            return period,datetime.combine(first,dt_time.min),datetime.combine(last+timedelta(days=1),dt_time.min)
        except (ValueError,OverflowError):
            raise ValueError("Choose a valid date range of no more than one year.")
    else: raise ValueError("Choose a valid report period.")
    return period,start,now


@admin_bp.route("/forgot-password",methods=["GET","POST"])
def forgot_password():
    if request.method=="POST":
        key=hashlib.sha256(("reset|"+(request.remote_addr or "")).encode()).hexdigest()
        cutoff=datetime.utcnow()-timedelta(minutes=15)
        recent=AdminLoginAttempt.query.filter(AdminLoginAttempt.identifier==key,
            AdminLoginAttempt.attempted_at>=cutoff).count()
        if recent>=3:
            flash("If this address has admin access, a password-reset link has been sent.","success")
            return redirect(url_for("admin.login"))
        db.session.add(AdminLoginAttempt(identifier=key))
        db.session.commit()
        email=request.form.get("email","").strip().lower()
        user=User.query.filter_by(email=email).first() if email else None
        if _admin_user(user):
            token=secrets.token_urlsafe(32)
            now=datetime.utcnow()
            AdminPasswordReset.query.filter_by(user_id=user.id,used_at=None).update({"used_at":now})
            reset=AdminPasswordReset(user_id=user.id,token_digest=hashlib.sha256(token.encode()).hexdigest(),
                expires_at=now+timedelta(hours=1),created_at=now)
            db.session.add(reset)
            db.session.flush()
            link=url_for("admin.reset_password",token=token,_external=True,_scheme="https")
            try:
                sent=_send_email("Reset your StockBridge admin password",user.email,
                    f"Hello {user.full_name},\n\nOpen this link to create a new StockBridge admin password:\n{link}\n\nThis link expires in one hour and works only once. If you did not request this, ignore this email. If this is also your business account, its password will change too.")
            except Exception:
                sent=False
                log_failure("admin_password_reset_email_failed")
            if not sent:
                db.session.rollback()
            else:
                db.session.commit()
        flash("If this address has admin access, a password-reset link has been sent.","success")
        return redirect(url_for("admin.login"))
    return render_template("admin/forgot_password.html")


@admin_bp.route("/reset-password/<token>",methods=["GET","POST"])
def reset_password(token):
    digest=hashlib.sha256(token.encode()).hexdigest()
    reset=AdminPasswordReset.query.filter_by(token_digest=digest,used_at=None).first()
    if not reset or reset.expires_at<datetime.utcnow() or not _admin_user(reset.user):
        return render_template("admin/reset_password.html",invalid=True),400
    if request.method=="POST":
        password=request.form.get("password","")
        if not valid_admin_password(password):
            return render_template("admin/reset_password.html",error="Use at least 12 characters and avoid common passwords."),400
        if password!=request.form.get("confirm_password",""):
            return render_template("admin/reset_password.html",error="Passwords do not match."),400
        changed=AdminPasswordReset.query.filter(AdminPasswordReset.id==reset.id,
            AdminPasswordReset.used_at.is_(None),AdminPasswordReset.expires_at>=datetime.utcnow()).update({"used_at":datetime.utcnow()})
        if changed!=1:
            db.session.rollback()
            return render_template("admin/reset_password.html",invalid=True),400
        reset.user.set_password(password)
        reset.user.admin_auth_version+=1
        AdminPasswordReset.query.filter(AdminPasswordReset.user_id==reset.user_id,
            AdminPasswordReset.used_at.is_(None)).update({"used_at":datetime.utcnow()})
        log("ADMIN_PASSWORD_RESET","Administrator reset password.",actor=reset.user)
        db.session.commit()
        logout_user()
        session.clear()
        flash("Password updated. Sign in with your new password.","success")
        return redirect(url_for("admin.login"))
    return render_template("admin/reset_password.html")

def overview():
    today=datetime.utcnow().date()
    start=datetime.combine(today,datetime.min.time())
    week=start-timedelta(days=7)
    month=start-timedelta(days=30)
    success=verified_payments_query()
    # Dual-role admins retain their business records, but are not customers.
    customer_ids=db.session.query(User.id).filter(User.role=="user",User.admin_enabled.is_(False))
    customer_business_ids=db.session.query(Business.id).filter(Business.user_id.in_(customer_ids))
    from app.admin.subscription_reporting import current_subscription_accounts, collected_subscription_payments
    from app.subscriptions.entitlements import EntitlementSnapshot
    owned_businesses = Business.query.filter(Business.user_id.in_(owner_query().with_entities(User.id))).all()
    customers = customer_query().all()
    snapshot = EntitlementSnapshot(customers)
    access = business_access_counts(owned_businesses, snapshot)
    return {
        "current_subscription_accounts": current_subscription_accounts(
            (user for user in customers if not user.staff_only), snapshot=snapshot,
        ),
        "subscription_payments": collected_subscription_payments(),
        "users":User.query.filter(User.id.in_(customer_ids)).count(),
        "active_users":User.query.filter(User.id.in_(customer_ids),User.suspended_at.is_(None)).count(),
        "suspended_users":User.query.filter(User.id.in_(customer_ids),User.suspended_at.isnot(None)).count(),
        "verified_users":User.query.filter(User.id.in_(customer_ids),User.email_verified_at.isnot(None)).count(),
        "unverified_users":User.query.filter(User.id.in_(customer_ids),User.email_verified_at.is_(None)).count(),
        "new_today":User.query.filter(User.id.in_(customer_ids),User.created_at>=start).count(),
        "new_week":User.query.filter(User.id.in_(customer_ids),User.created_at>=week).count(),
        "new_month":User.query.filter(User.id.in_(customer_ids),User.created_at>=month).count(),
        "businesses":len(owned_businesses),
        "active_businesses":access["active_businesses"],
        "suspended_businesses":sum(b.suspended_at is not None for b in owned_businesses),
        "paid_users":Business.query.filter(Business.id.in_(customer_business_ids),Business.subscription_plan=="lifetime",Business.subscription_status=="active").count(),
        "unpaid_users":access["unpaid_users"],
        "successful_payments":success.count(),
        "pending_payments":Payment.query.filter(Payment.product=="lifetime",Payment.status.in_(("initialized","pending"))).count(),
        "failed_payments":Payment.query.filter_by(status="failed",product="lifetime").count(),
        "access_revenue_kobo":success.with_entities(func.coalesce(func.sum(Payment.amount_kobo),0)).scalar(),
        "sales_today":Sale.query.filter(Sale.business_id.in_(customer_business_ids),Sale.sold_at>=start,Sale.voided_at.is_(None)).count(),
        "products":Product.query.filter(Product.business_id.in_(customer_business_ids)).count(),
        "sales":Sale.query.filter(Sale.business_id.in_(customer_business_ids)).count(),
        "restocks":Restock.query.filter(Restock.business_id.in_(customer_business_ids)).count(),
        "expenses":Expense.query.filter(Expense.business_id.in_(customer_business_ids)).count(),
    }

@admin_bp.get("/")
@admin_required
def index():
    period = request.args.get("period", "30d")
    if period not in PERIODS:
        abort(400, description="Choose Today, 7 days or 30 days.")
    return render_template("admin/index.html", metrics=dashboard(period, verified_payments_query()), period=period)


@admin_bp.get("/dashboard/search")
@admin_required
def dashboard_search():
    destinations = {"customers": "admin.users", "businesses": "admin.businesses", "payments": "admin.payments"}
    endpoint = destinations.get(request.args.get("scope"), "admin.users")
    return redirect(url_for(endpoint, q=request.args.get("q", "").strip()[:100]))


@admin_bp.get("/users")
@admin_required
def users():
    from app.admin import customer_data
    now = datetime.utcnow()
    status = request.args.get('status', 'all')
    if status not in customer_data.FILTERS:
        abort(400)
    q = request.args.get('q', '').strip()[:100]
    rows = customer_data.filtered_customers(q, status, now).paginate(
        page=request.args.get('page', 1, type=int), per_page=25, error_out=False)
    loader = customer_data.CustomerRows(rows.items, now)
    return render_template('admin/users.html', rows=rows, q=q, status=status,
                           customer_rows=[loader.serialize(u) for u in rows.items],
                           summary=customer_data.summary(now))

@admin_bp.get("/users/<int:user_id>")
@admin_required
def user_detail(user_id):
    user=customer_query().filter(User.id==user_id).first_or_404()
    businesses = sorted(user.businesses, key=lambda b: b.id)
    business = businesses[0] if businesses else None
    business_ids = [b.id for b in businesses]

    metrics = {
        "products": Product.query.filter(Product.business_id.in_(business_ids)).count(),
        "sales": Sale.query.filter(Sale.business_id.in_(business_ids)).count(),
        "restocks": Restock.query.filter(Restock.business_id.in_(business_ids)).count(),
        "expenses": Expense.query.filter(Expense.business_id.in_(business_ids)).count(),
    }
    payments=Payment.query.filter(func.lower(Payment.customer_email)==user.email.lower()).order_by(Payment.created_at.desc()).limit(10).all()
    activity = AuditLog.query.filter(
        or_(
            and_(
                AuditLog.target_type == "user",
                AuditLog.target_id == user.id,
            ),
            AuditLog.business_id.in_(business_ids),
        )
    ).order_by(
        AuditLog.created_at.desc()
    ).limit(10).all()
    return render_template(
        "admin/user_detail.html",
        user=user,
        business=business,
        businesses=businesses,
        metrics=metrics,
        payments=payments,
        activity=activity,
        detail=customer_tools.data.details(user, datetime.utcnow()),
        local_time=customer_tools.data.local_time,
        masked_description=customer_tools.data.masked_description,
    )
def change_suspension(record, *, target_type, description, business_id):
    """Compare and set status in the same transaction as its audit event.

    PostgreSQL rechecks the UPDATE predicate after a competing writer commits.
    Only the winner can create an audit event or send a notification.
    """
    desired = request.form.get("desired_state")
    expected = request.form.get("expected_state")
    if desired not in {"suspend", "reactivate"} or expected not in {"active", "suspended"}:
        abort(400, description="Invalid status request. Refresh this page.")
    if (desired == "suspend") != (expected == "active"):
        abort(409, description="Status request conflicts. Refresh this page.")
    model = type(record)
    predicate = model.suspended_at.is_(None)
    if expected == "suspended":
        try:
            stamp = datetime.fromisoformat(request.form.get("expected_suspended_at", ""))
        except ValueError:
            abort(400, description="Invalid status request. Refresh this page.")
        predicate = model.suspended_at == stamp
    reason = request.form.get("reason", "").strip()
    if len(reason) > 300:
        abort(400, description="Use a reason of at most 300 characters.")
    try:
        claimed = db.session.execute(update(model).where(model.id == record.id, predicate)
            .values(suspended_at=datetime.utcnow() if desired == "suspend" else None)
            .returning(model.id), execution_options={"synchronize_session": False}).scalar_one_or_none()
        if claimed is None:
            db.session.rollback()
            abort(409, description="Status changed or request already applied. Refresh this page.")
        action = target_type.upper() + ("_SUSPENDED" if desired == "suspend" else "_REACTIVATED")
        log(action, description + reason, actor=current_user, business_id=business_id,
            target_type=target_type, target_id=record.id)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    db.session.refresh(record)
    return desired == "reactivate"


@admin_bp.post("/users/<int:user_id>/suspension")
@admin_required
def suspend_user(user_id):
    user=User.query.filter_by(id=user_id,role="user").first_or_404()
    if user.admin_enabled:
        abort(403)
    if request.form.get("confirm")!="on":
        flash("Confirm the account status change first.","error")
        return redirect(url_for("admin.user_detail",user_id=user_id))
    reason=request.form.get("reason","").strip()
    if not reason:
        flash("Enter a reason for this action.","error")
        return redirect(url_for("admin.user_detail",user_id=user_id))
    active=change_suspension(user, target_type="user", description=f"Account {user.email}: ",
        business_id=user.businesses[0].id if user.businesses else None)
    safely_send(send_account_status_email,user,active=active)
    return redirect(url_for("admin.user_detail",user_id=user_id))

@admin_bp.get("/businesses")
@admin_required
def businesses():
    from app.admin import business_data
    q=request.args.get("q","").strip()[:100]
    status=request.args.get('status','all')
    if status not in business_data.FILTERS:
        abort(400)
    now=datetime.utcnow()
    rows=business_data.query(q,status,now=now).paginate(page=request.args.get('page',1,type=int),per_page=25,error_out=False)
    return render_template('admin/businesses.html',rows=rows,q=q,status=status,
        business_rows=business_data.serialize(rows.items,now),summary=business_data.summary(now))


@admin_bp.get('/businesses/export.csv')
@admin_required
def businesses_export():
    from app.admin import business_data
    from app.admin.customers import csv_response
    q=request.args.get('q','').strip()[:100]
    status=request.args.get('status','all')
    if status not in business_data.FILTERS:
        abort(400)
    now=datetime.utcnow()
    def batches():
        cursor=None
        while True:
            query=business_data.query(q,status,now=now).filter(Business.created_at<=now)
            if cursor:
                stamp,identifier=cursor
                query=query.filter(or_(Business.created_at<stamp,
                    (Business.created_at==stamp)&(Business.id<identifier)))
            batch=query.limit(100).all()
            if not batch:
                break
            cursor=(batch[-1].created_at,batch[-1].id)
            yield [[b['name'],b['owner_email'],b['plan'],b['status'],b['created'],
                    f"{b['last_sale']} · Sale #{b['last_sale_id']}" if b['last_sale_id'] else 'No sales yet',
                    len(b['team']),'; '.join(f"{e['email']} · {e['role']}" for e in b['team'])]
                   for b in business_data.serialize(batch,now)]
    return csv_response('StockBridge-businesses.csv',
        ['Business','Owner (masked)','Plan','Status','Created','Last sale','Employee count','Team (masked)'],batches)

@admin_bp.get("/businesses/<int:business_id>")
@admin_required
def business_detail(business_id):
    b=customer_business_query().filter(Business.id==business_id).first_or_404()
    metrics={"products":Product.query.filter_by(business_id=b.id).count(),
        "sales":Sale.query.filter_by(business_id=b.id).count(),
        "restocks":Restock.query.filter_by(business_id=b.id).count(),
        "expenses":Expense.query.filter_by(business_id=b.id).count()}
    payments=Payment.query.filter_by(business_id=b.id).order_by(Payment.created_at.desc()).limit(5).all()
    activity=AuditLog.query.filter_by(business_id=b.id).order_by(AuditLog.created_at.desc()).limit(8).all()
    return render_template("admin/business_detail.html",business=b,metrics=metrics,payments=payments,activity=activity)

@admin_bp.post("/businesses/<int:business_id>/suspension")
@admin_required
def suspend_business(business_id):
    b=db.session.get(Business,business_id)
    if not b: abort(404)
    if b.owner.role=="admin" or b.owner.admin_enabled:
        abort(403)
    if request.form.get("confirm")!="on":
        flash("Confirm the business status change first.","error")
        return redirect(url_for("admin.business_detail",business_id=b.id))
    reason=request.form.get("reason","").strip()
    if not reason:
        flash("Enter a reason for this action.","error")
        return redirect(url_for("admin.business_detail",business_id=b.id))
    active=change_suspension(b, target_type="business", description=f"Business {b.id}: ", business_id=b.id)
    safely_send(send_account_status_email,b.owner,active=active,business=b)
    return redirect(url_for("admin.business_detail",business_id=b.id))

def subscription_payments_query():
    from app.admin.subscription_reporting import subscription_payments_query as query
    return query()


@admin_bp.get("/payments")
@admin_required
def payments():
    from app.admin import payment_data
    status=request.args.get("status","all")
    if status not in payment_data.FILTERS:
        status='all'
    q = request.args.get("q", "").strip()[:100]
    now=datetime.utcnow()
    if request.args.get('format')=='csv':
        from app.admin.customers import csv_response
        def batches():
            cursor=None
            while True:
                query=payment_data.filtered(q,status).filter(Payment.created_at<=now)
                if cursor:
                    stamp,identifier=cursor
                    query=query.filter(or_(Payment.created_at<stamp,
                        (Payment.created_at==stamp)&(Payment.id<identifier)))
                batch=query.limit(100).all()
                if not batch:
                    break
                cursor=(batch[-1].created_at,batch[-1].id)
                yield [[p['created'],p['customer'],p['email'],p['business'],p['plan'],p['interval'],
                        p['amount'],p['status'],p['reference']] for p in payment_data.serialize(batch)]
        return csv_response('StockBridge-payments.csv',
            ['Date','Customer','Masked email','Business','Plan','Interval','Amount','Status','Reference'],batches)
    rows=payment_data.filtered(q,status).paginate(page=request.args.get('page',1,type=int),per_page=25,error_out=False)
    return render_template('admin/payments.html',rows=rows,status=status,q=q,
        payment_rows=payment_data.serialize(rows.items),summary=payment_data.summary(now),money=payment_data.money)


@admin_bp.get("/payments/<int:payment_id>")
@admin_required
def payment_detail(payment_id):
    from app.admin.payment_data import money
    from app.admin.customer_data import masked_email, local_time
    payment=subscription_payments_query().options(joinedload(Payment.business)).filter(Payment.id==payment_id).first_or_404()
    owner=customer_query().filter(User.id==payment.user_id).first_or_404()
    # A corrupt/mismatched subscription reference must not disclose another account.
    subscription=RecurringSubscription.query.join(Business, RecurringSubscription.business_id==Business.id).filter(
        RecurringSubscription.id==payment.subscription_id, RecurringSubscription.user_id==owner.id,
        Business.user_id==owner.id)
    if payment.business_id is not None:
        subscription=subscription.filter(RecurringSubscription.business_id==payment.business_id)
    return render_template("admin/payment_detail.html",payment=payment,owner=owner,subscription=subscription.first(),
        masked_email=masked_email,local_time=local_time,money=money)


@admin_bp.post("/payments/<int:payment_id>/verify")
@admin_required
def verify_payment(payment_id):
    payment=Payment.query.filter_by(id=payment_id,product="lifetime").first_or_404()
    destination=url_for("admin.payment_detail",payment_id=payment.id)
    if request.form.get("confirm")!="on":
        flash("Confirm the Paystack verification first.","error")
        return redirect(destination)
    users=User.query.filter(func.lower(User.email)==payment.customer_email.lower()).limit(2).all()
    user=users[0] if len(users)==1 else None
    business=user.businesses[0] if user and len(user.businesses)==1 else None
    if (not user or user.role!="user" or user.admin_enabled or user.suspended_at or not business
            or (payment.business_id is not None and payment.business_id!=business.id)
            or payment.amount_kobo!=current_app.config["LIFETIME_PRICE_NAIRA"]*100
            or payment.currency!="NGN" or payment.provider!="paystack"):
        flash("Payment and customer details need review before verification.","error")
        return redirect(destination)
    from app.payments.routes import _confirm, _activate_account, _payments_configured
    if not _payments_configured():
        flash("Paystack verification is unavailable in this environment.","error")
        return redirect(destination)
    log("PAYMENT_VERIFICATION_REQUESTED",f"Admin requested Paystack verification for {payment.reference}.",
        actor=current_user,business_id=business.id,target_type="payment",target_id=payment.id)
    db.session.commit()
    try:
        details=verify_transaction(current_app.config["PAYSTACK_SECRET_KEY"],payment.reference)
    except PaystackError:
        current_app.logger.warning("Admin Paystack verification unavailable for payment %s",payment.id)
        flash("Paystack could not verify this payment. No access was granted.","error")
        return redirect(destination)
    if not _confirm(payment,details,commit=False):
        db.session.rollback()
        flash("Paystack did not confirm the expected transaction. No access was granted.","error")
        return redirect(destination)
    grant_needed=business.subscription_status!="active" or business.subscription_plan!="lifetime"
    if not _activate_account(payment,commit=False):
        db.session.rollback()
        flash("The payment could not be matched to its customer. No access was granted.","error")
        return redirect(destination)
    log("PAYMENT_VERIFICATION_SUCCESSFUL",f"Admin verified Paystack reference {payment.reference}.",
        actor=current_user,business_id=business.id,target_type="payment",target_id=payment.id)
    if grant_needed:
        log("LIFETIME_ACCESS_GRANTED",f"Lifetime Access confirmed for business {business.id}.",
            actor=current_user,business_id=business.id,target_type="business",target_id=business.id)
    db.session.commit()
    safely_send(send_access_receipt_once,payment)
    flash("Paystack verified the payment and Lifetime Access is active.","success")
    return redirect(destination)

@admin_bp.get("/activity")
@admin_required
def activity():
    action=request.args.get("action","all")
    q=request.args.get("q","").strip()[:100]
    query=AuditLog.query.options(joinedload(AuditLog.actor),joinedload(AuditLog.business))
    if action=="security":
        query=query.filter(AuditLog.action.in_(("ADMIN_LOGIN_SUCCESS","ADMIN_LOGIN_FAILED","ADMIN_LOGOUT","ADMIN_PASSWORD_RESET",
            "ADMIN_MFA_ENROLLED","ADMIN_MFA_FAILED","ADMIN_MFA_RECOVERY_USED")))
    elif action in ("account","management"):
        query=query.filter(AuditLog.action.in_(("USER_SUSPENDED","USER_REACTIVATED","BUSINESS_SUSPENDED","BUSINESS_REACTIVATED")))
    elif action=="payments": query=query.filter(or_(AuditLog.action.like("%PAYMENT%"),AuditLog.action=="LIFETIME_ACCESS_GRANTED"))
    elif action=="admin": query=query.filter(AuditLog.action.like("ADMIN_%"))
    elif action=="customers": query=query.filter(or_(AuditLog.action.like("USER_%"),AuditLog.action=="LIFETIME_ACCESS_GRANTED"))
    if q:
        matching_users=db.session.query(User.id).filter(or_(User.email.ilike(f"%{q}%"),User.full_name.ilike(f"%{q}%")))
        query=query.filter(or_(AuditLog.description.ilike(f"%{q}%"),
            AuditLog.actor.has(User.email.ilike(f"%{q}%")),
            AuditLog.business.has(Business.name.ilike(f"%{q}%")),
            and_(AuditLog.target_type=="user",AuditLog.target_id.in_(matching_users))))
    rows=query.order_by(AuditLog.created_at.desc()).paginate(page=request.args.get("page",1,type=int),per_page=30,error_out=False)
    return render_template("admin/activity.html",rows=rows,action=action,q=q)

@admin_bp.get("/reports")
@admin_required
def reports():
    try:
        period,start,end=report_period()
    except ValueError as error:
        return render_template("admin/reports.html",metrics=None,error=str(error),period="30d"),400
    metrics=period_metrics(start,end)
    return render_template("admin/reports.html",metrics=metrics,period=period,start=start.date(),end=(end-timedelta(microseconds=1)).date())

def period_metrics(start,end, *, current_accounts=None):
    from app.admin.subscription_reporting import current_subscription_accounts, collected_subscription_payments
    verified=verified_payments_query().filter(Payment.paid_at>=start,Payment.paid_at<end)
    if current_accounts is None:
        current_accounts=current_subscription_accounts(customer_query().all())
    return {
        "current_subscription_accounts": current_accounts,
        "subscription_payments": collected_subscription_payments(start,end),
        "registrations":customer_query().filter(User.created_at>=start,User.created_at<end).count(),
        "verified_customers":customer_query().filter(User.email_verified_at>=start,User.email_verified_at<end).count(),
        "purchases":verified.count(),
        "revenue_kobo":verified.with_entities(func.coalesce(func.sum(Payment.amount_kobo),0)).scalar(),
        "pending":Payment.query.filter(Payment.product=="lifetime",Payment.status.in_(("initialized","pending")),Payment.created_at>=start,Payment.created_at<end).count(),
        "failed":Payment.query.filter(Payment.product=="lifetime",Payment.status=="failed",Payment.created_at>=start,Payment.created_at<end).count(),
        "active_now":customer_query().filter(User.suspended_at.is_(None)).count(),
        "suspended_now":customer_query().filter(User.suspended_at.isnot(None)).count(),
    }

@admin_bp.get("/notifications")
@admin_required
def notifications():
    pending=Payment.query.filter(Payment.product=="lifetime",Payment.status.in_(("initialized","pending")),
        Payment.created_at<datetime.utcnow()-timedelta(days=2)).order_by(Payment.created_at.asc()).limit(10).all()
    payment_issues=payment_issues_query().filter(Payment.status=="success").order_by(Payment.created_at.desc()).limit(10).all()
    failed=Payment.query.filter_by(product="lifetime",status="failed").order_by(Payment.created_at.desc()).limit(10).all()
    recent=AuditLog.query.filter(AuditLog.action.in_(("USER_REGISTERED","ACCESS_PAYMENT_SUCCESSFUL","USER_SUSPENDED","BUSINESS_SUSPENDED"))).order_by(AuditLog.created_at.desc()).limit(20).all()
    # An old active business marker is not itself a current entitlement.
    # Do not flag intentionally suspended accounts/businesses as billing anomalies.
    from app.subscriptions.entitlements import EntitlementSnapshot
    entitlement_now=datetime.utcnow()
    candidates=customer_business_query().filter(Business.subscription_status=="active",
        Business.suspended_at.is_(None),User.suspended_at.is_(None)).options(joinedload(Business.owner)).order_by(Business.id).all()
    snapshot=EntitlementSnapshot([b.owner for b in candidates],now=entitlement_now)
    access_without_payment=[]
    legacy_ids={p.business_id for p in verified_payments_query().all()}
    for business in candidates:
        if snapshot.access(business.owner,business=business).can_write:
            continue
        if not current_app.config.get("SUBSCRIPTIONS_ENABLED") and business.id in legacy_ids:
            continue
        access_without_payment.append(business)
        if len(access_without_payment)==10:
            break
    security_alerts=db.session.query(AdminLoginAttempt.identifier).filter(
        AdminLoginAttempt.attempted_at>=datetime.utcnow()-timedelta(minutes=15)
    ).group_by(AdminLoginAttempt.identifier).having(func.count(AdminLoginAttempt.id)>=5).count()
    return render_template("admin/notifications.html",pending=pending,failed=failed,payment_issues=payment_issues,
        access_without_payment=access_without_payment,recent=recent,security_alerts=security_alerts)

@admin_api_bp.get("/dashboard")
@admin_required
def api_dashboard():
    return jsonify(overview())

@admin_api_bp.get("/users")
@admin_required
def api_users():
    users=customer_query().order_by(User.id.desc()).limit(100).all()
    return jsonify(users=[{"id":u.id,"name":u.full_name,"email":u.email,"suspended":bool(u.suspended_at)} for u in users])

@admin_api_bp.get("/users/<int:user_id>")
@admin_required
def api_user(user_id):
    u=customer_query().filter(User.id==user_id).first_or_404()
    return jsonify(id=u.id,name=u.full_name,email=u.email,suspended=bool(u.suspended_at),
        business_ids=[b.id for b in u.businesses])

@admin_api_bp.get("/businesses")
@admin_required
def api_businesses():
    rows=customer_business_query().order_by(Business.id.desc()).limit(100).all()
    return jsonify(businesses=[{"id":b.id,"name":b.name,"owner_id":b.user_id,"suspended":bool(b.suspended_at)} for b in rows])

@admin_api_bp.get("/businesses/<int:business_id>")
@admin_required
def api_business(business_id):
    b=db.session.get(Business,business_id)
    if not b or b.owner.role!="user" or b.owner.admin_enabled: abort(404)
    return jsonify(id=b.id,name=b.name,owner_id=b.user_id,suspended=bool(b.suspended_at),access=b.subscription_status)

@admin_api_bp.get("/payments")
@admin_required
def api_payments():
    product=request.args.get("product","lifetime")
    if product not in ("lifetime","subscription"):
        return jsonify(error="Choose lifetime or subscription payments."),400
    if product=="subscription":
        status=request.args.get("status","all")
        query=subscription_payments_query()
        if status in ("success","failed","pending"):
            query=query.filter(Payment.status.in_(("initialized","pending")) if status=="pending" else Payment.status==status)
        rows=query.order_by(Payment.created_at.desc(),Payment.id.desc()).limit(100).all()
        return jsonify(product=product,payments=[{
            "id":p.id,"business_id":p.business_id,"reference":p.reference,
            "amount_kobo":p.amount_kobo,"status":p.status,"user_id":p.user_id,
            "plan_code":p.plan_code,"billing_interval":p.billing_interval,
            "paid_at":p.paid_at.isoformat() if p.paid_at else None} for p in rows])
    # Preserve the historical default payload for existing API consumers.
    rows=Payment.query.filter_by(product="lifetime").order_by(Payment.id.desc()).limit(100).all()
    return jsonify(payments=[{"id":p.id,"business_id":p.business_id,"reference":p.reference,
        "amount_kobo":p.amount_kobo,"status":p.status} for p in rows])

@admin_api_bp.get("/activity")
@admin_required
def api_activity():
    rows=AuditLog.query.order_by(AuditLog.id.desc()).limit(100).all()
    return jsonify(activity=[{"id":row.id,"action":row.action,"actor_id":row.actor_id,
        "business_id":row.business_id,"created_at":row.created_at.isoformat()} for row in rows])

@admin_api_bp.get("/reports")
@admin_required
def api_reports():
    try:
        period,start,end=report_period()
    except ValueError as error:
        return jsonify(error=str(error)),400
    metrics=overview()
    metrics["period_metrics"]=period_metrics(start,end,
        current_accounts=metrics["current_subscription_accounts"])
    return jsonify(metrics)

# Register customer tools after the shared admin decorator and helpers exist.
from app.admin import customers as customer_tools
from app.admin import payment_checks as payment_check_tools
