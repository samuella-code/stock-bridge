import hashlib
from datetime import datetime, timedelta
from functools import wraps
from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_user, logout_user
from sqlalchemy import func, or_
from werkzeug.security import generate_password_hash, check_password_hash
from app import db
from app.models import (AdminLoginAttempt, AuditLog, Business, Expense,
                        Payment, Product, Restock, Sale, User)

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
        if not (current_user.role=="admin" or current_user.admin_enabled) or not session.get("admin_session") or current_user.suspended_at:
            abort(403)
        return view(*args,**kwargs)
    return wrapped

def log(action,description,actor=None,business_id=None):
    if actor:
        actor.last_activity_at=datetime.utcnow()
    db.session.add(AuditLog(action=action,description=description[:300],
        actor_id=actor.id if actor else None,business_id=business_id))

def _login_key(email):
    # The server-observed address limits distributed guesses against an account.
    raw=(email+"|"+(request.remote_addr or "")).encode()
    return hashlib.sha256(raw).hexdigest()

@admin_bp.route("/login",methods=["GET","POST"])
def login():
    if current_user.is_authenticated:
        if (current_user.role=="admin" or current_user.admin_enabled) and session.get("admin_session"):
            return redirect(url_for("admin.index"))
        if current_user.role=="admin":
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
            db.session.commit()
            return render_template("admin/login.html",error="Invalid admin credentials."),401
        AdminLoginAttempt.query.filter_by(identifier=key).delete()
        session.clear()
        login_user(user,fresh=True)
        session["admin_session"]=True
        user.last_activity_at=datetime.utcnow()
        log("ADMIN_LOGIN","Administrator signed in.",actor=user)
        db.session.commit()
        return redirect(url_for("admin.index"))
    return render_template("admin/login.html")

@admin_bp.post("/logout")
@admin_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for("admin.login"))

def overview():
    today=datetime.utcnow().date()
    start=datetime.combine(today,datetime.min.time())
    week=start-timedelta(days=7)
    month=start-timedelta(days=30)
    success=Payment.query.filter_by(status="success",product="lifetime")
    return {
        "users":User.query.filter_by(role="user").count(),
        "new_today":User.query.filter(User.role=="user",User.created_at>=start).count(),
        "new_week":User.query.filter(User.role=="user",User.created_at>=week).count(),
        "new_month":User.query.filter(User.role=="user",User.created_at>=month).count(),
        "businesses":Business.query.count(),
        "active_businesses":Business.query.filter(Business.suspended_at.is_(None),Business.subscription_status=="active").count(),
        "paid_users":Business.query.filter(Business.subscription_plan=="lifetime",Business.subscription_status=="active").count(),
        "successful_payments":success.count(),
        "pending_payments":Payment.query.filter(Payment.product=="lifetime",Payment.status.in_(("initialized","pending"))).count(),
        "failed_payments":Payment.query.filter_by(status="failed",product="lifetime").count(),
        "access_revenue_kobo":db.session.query(func.coalesce(func.sum(Payment.amount_kobo),0)).filter(Payment.status=="success",Payment.product=="lifetime",Payment.currency=="NGN").scalar(),
        "sales_today":Sale.query.filter(Sale.sold_at>=start,Sale.voided_at.is_(None)).count(),
        "products":Product.query.count(),
        "sales":Sale.query.count(),
        "restocks":Restock.query.count(),
        "expenses":Expense.query.count(),
    }

def recent_activity():
    # Events from before the audit table existed remain visible via their records.
    logs=AuditLog.query.order_by(AuditLog.created_at.desc()).limit(30).all()
    return logs

@admin_bp.get("/")
@admin_required
def index():
    return render_template("admin/index.html",metrics=overview(),activity=recent_activity())

@admin_bp.get("/users")
@admin_required
def users():
    q=request.args.get("q","").strip()[:100]
    status=request.args.get("status","all")
    query=User.query.filter(User.role=="user")
    if q: query=query.filter(or_(User.email.ilike(f"%{q}%"),User.full_name.ilike(f"%{q}%")))
    if status=="suspended": query=query.filter(User.suspended_at.isnot(None))
    elif status=="active": query=query.filter(User.suspended_at.is_(None))
    elif status=="paid": query=query.join(Business).filter(Business.subscription_plan=="lifetime",Business.subscription_status=="active")
    elif status=="unpaid": query=query.join(Business).filter(Business.subscription_status!="active")
    rows=query.order_by(User.created_at.desc()).paginate(page=request.args.get("page",1,type=int),per_page=25,error_out=False)
    return render_template("admin/users.html",rows=rows,q=q,status=status)

@admin_bp.get("/users/<int:user_id>")
@admin_required
def user_detail(user_id):
    user=User.query.filter_by(id=user_id,role="user").first_or_404()
    business=user.businesses[0] if user.businesses else None
    metrics={"products":Product.query.filter_by(business_id=business.id).count() if business else 0,
        "sales":Sale.query.filter_by(business_id=business.id).count() if business else 0,
        "restocks":Restock.query.filter_by(business_id=business.id).count() if business else 0,
        "expenses":Expense.query.filter_by(business_id=business.id).count() if business else 0}
    payments=Payment.query.filter_by(customer_email=user.email).order_by(Payment.created_at.desc()).limit(10).all()
    return render_template("admin/user_detail.html",user=user,business=business,metrics=metrics,payments=payments)

@admin_bp.post("/users/<int:user_id>/suspension")
@admin_required
def suspend_user(user_id):
    user=User.query.filter_by(id=user_id,role="user").first_or_404()
    if user.admin_enabled:
        abort(403)
    reason=request.form.get("reason","").strip()
    if not reason:
        flash("Enter a reason for this action.","error")
        return redirect(url_for("admin.user_detail",user_id=user_id))
    user.suspended_at=None if user.suspended_at else datetime.utcnow()
    log("USER_REACTIVATED" if user.suspended_at is None else "USER_SUSPENDED",
        f"Account {user.email}: {reason}",actor=current_user,
        business_id=user.businesses[0].id if user.businesses else None)
    db.session.commit()
    return redirect(url_for("admin.user_detail",user_id=user_id))

@admin_bp.get("/businesses")
@admin_required
def businesses():
    q=request.args.get("q","").strip()[:100]
    query=Business.query.join(User)
    if q: query=query.filter(or_(Business.name.ilike(f"%{q}%"),User.email.ilike(f"%{q}%")))
    rows=query.order_by(Business.created_at.desc()).paginate(page=request.args.get("page",1,type=int),per_page=25,error_out=False)
    return render_template("admin/businesses.html",rows=rows,q=q)

@admin_bp.get("/businesses/<int:business_id>")
@admin_required
def business_detail(business_id):
    b=db.session.get(Business,business_id)
    if not b: abort(404)
    metrics={"products":Product.query.filter_by(business_id=b.id).count(),
        "sales":Sale.query.filter_by(business_id=b.id).count(),
        "restocks":Restock.query.filter_by(business_id=b.id).count(),
        "expenses":Expense.query.filter_by(business_id=b.id).count()}
    return render_template("admin/business_detail.html",business=b,metrics=metrics)

@admin_bp.post("/businesses/<int:business_id>/suspension")
@admin_required
def suspend_business(business_id):
    b=db.session.get(Business,business_id)
    if not b: abort(404)
    if b.user_id==current_user.id:
        abort(403)
    reason=request.form.get("reason","").strip()
    if not reason:
        flash("Enter a reason for this action.","error")
        return redirect(url_for("admin.business_detail",business_id=b.id))
    b.suspended_at=None if b.suspended_at else datetime.utcnow()
    log("BUSINESS_REACTIVATED" if b.suspended_at is None else "BUSINESS_SUSPENDED",
        f"Business {b.id}: {reason}",actor=current_user,business_id=b.id)
    db.session.commit()
    return redirect(url_for("admin.business_detail",business_id=b.id))

@admin_bp.get("/payments")
@admin_required
def payments():
    status=request.args.get("status","all")
    query=Payment.query.filter_by(product="lifetime")
    if status in ("success","failed","pending"):
        query=query.filter(Payment.status.in_(("initialized","pending")) if status=="pending" else Payment.status==status)
    rows=query.order_by(Payment.created_at.desc()).paginate(page=request.args.get("page",1,type=int),per_page=25,error_out=False)
    return render_template("admin/payments.html",rows=rows,status=status)

@admin_bp.get("/activity")
@admin_required
def activity():
    rows=AuditLog.query.order_by(AuditLog.created_at.desc()).paginate(page=request.args.get("page",1,type=int),per_page=30,error_out=False)
    return render_template("admin/activity.html",rows=rows)

@admin_bp.get("/reports")
@admin_required
def reports():
    return render_template("admin/reports.html",metrics=overview())

@admin_bp.get("/notifications")
@admin_required
def notifications():
    pending=Payment.query.filter(Payment.product=="lifetime",Payment.status.in_(("initialized","pending"))).order_by(Payment.created_at.desc()).limit(10).all()
    recent=AuditLog.query.filter(AuditLog.action.in_(("USER_REGISTERED","ACCESS_PAYMENT_SUCCESSFUL","USER_SUSPENDED","BUSINESS_SUSPENDED"))).order_by(AuditLog.created_at.desc()).limit(20).all()
    return render_template("admin/notifications.html",pending=pending,recent=recent)

@admin_api_bp.get("/dashboard")
@admin_required
def api_dashboard():
    return jsonify(overview())

@admin_api_bp.get("/users")
@admin_required
def api_users():
    users=User.query.filter_by(role="user").order_by(User.id.desc()).limit(100).all()
    return jsonify(users=[{"id":u.id,"name":u.full_name,"email":u.email,"suspended":bool(u.suspended_at)} for u in users])

@admin_api_bp.get("/users/<int:user_id>")
@admin_required
def api_user(user_id):
    u=User.query.filter_by(id=user_id,role="user").first_or_404()
    return jsonify(id=u.id,name=u.full_name,email=u.email,suspended=bool(u.suspended_at),
        business_ids=[b.id for b in u.businesses])

@admin_api_bp.get("/businesses")
@admin_required
def api_businesses():
    rows=Business.query.order_by(Business.id.desc()).limit(100).all()
    return jsonify(businesses=[{"id":b.id,"name":b.name,"owner_id":b.user_id,"suspended":bool(b.suspended_at)} for b in rows])

@admin_api_bp.get("/businesses/<int:business_id>")
@admin_required
def api_business(business_id):
    b=db.session.get(Business,business_id)
    if not b: abort(404)
    return jsonify(id=b.id,name=b.name,owner_id=b.user_id,suspended=bool(b.suspended_at),access=b.subscription_status)

@admin_api_bp.get("/payments")
@admin_required
def api_payments():
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
    return jsonify(overview())
