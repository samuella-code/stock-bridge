from datetime import datetime,timedelta
from flask import Blueprint,abort,flash,redirect,render_template,request,url_for
from flask_login import current_user,login_required
from sqlalchemy import func
from app import db
from app.models import Product,Sale
restocking_bp=Blueprint("restocking",__name__,url_prefix="/restocking")
def recommendations(b):
 since=datetime.utcnow()-timedelta(days=30); rows=[]
 for p in Product.query.filter_by(business_id=b.id).all():
  first=db.session.query(func.min(Sale.sold_at)).filter(Sale.product_id==p.id,Sale.sold_at>=since).scalar(); sold=db.session.query(func.coalesce(func.sum(Sale.quantity),0)).filter(Sale.product_id==p.id,Sale.sold_at>=since).scalar()
  observed=max(1,min(30,(datetime.utcnow()-first).days+1)) if first else 30; avg=float(sold)/observed; point=(avg*p.supplier_lead_time)+p.safety_stock; days=p.stock_quantity/avg if avg else None; suggested=max(0,int(round(point+avg*7-p.stock_quantity)))
  priority="Urgent" if avg and p.stock_quantity<=point else "Watch" if p.is_low_stock else "Healthy"; rows.append({"product":p,"avg":avg,"point":point,"days":days,"suggested":suggested,"priority":priority})
 return sorted(rows,key=lambda x:{"Urgent":0,"Watch":1,"Healthy":2}[x["priority"]])
@restocking_bp.get("/")
@login_required
def index(): b=current_user.businesses[0]; return render_template("restocking/index.html",business=b,recommendations=recommendations(b))
@restocking_bp.post("/<int:product_id>/settings")
@login_required
def settings(product_id):
 b=current_user.businesses[0]; p=db.session.get(Product,product_id)
 if not p or p.business_id!=b.id: abort(404)
 try: lead=int(request.form["supplier_lead_time"]); safety=int(request.form["safety_stock"]); minimum=int(request.form["minimum_stock_level"]); assert min(lead,safety,minimum)>=0
 except (ValueError,KeyError,AssertionError): flash("Restocking settings must be whole numbers of zero or more.","error")
 else: p.supplier_lead_time=lead; p.safety_stock=safety; p.minimum_stock_level=minimum; db.session.commit(); flash(f"Restocking settings updated for {p.name}.","success")
 return redirect(url_for("restocking.index"))
