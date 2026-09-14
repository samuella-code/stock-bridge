from datetime import datetime,timedelta
from flask import Blueprint,render_template
from flask_login import current_user,login_required
from sqlalchemy import func
from app import db
from app.models import Product,Sale
restocking_bp=Blueprint("restocking",__name__,url_prefix="/restocking")
@restocking_bp.route("/")
@login_required
def index():
 b=current_user.businesses[0]; since=datetime.utcnow()-timedelta(days=30); recommendations=[]
 for p in Product.query.filter_by(business_id=b.id).all():
  sold=db.session.query(func.coalesce(func.sum(Sale.quantity),0)).filter(Sale.product_id==p.id,Sale.sold_at>=since).scalar()
  avg=float(sold)/30; point=(avg*p.supplier_lead_time)+p.safety_stock; days=(p.stock_quantity/avg) if avg else None; suggested=max(0,int(round(point-p.stock_quantity+avg*7)))
  priority="Urgent" if p.stock_quantity<=point and avg else "Watch" if p.is_low_stock else "Healthy"
  recommendations.append({"product":p,"avg":avg,"point":point,"days":days,"suggested":suggested,"priority":priority})
 recommendations.sort(key=lambda x:{"Urgent":0,"Watch":1,"Healthy":2}[x["priority"]])
 return render_template("restocking/index.html",business=b,recommendations=recommendations)
