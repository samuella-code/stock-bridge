from datetime import datetime
from decimal import Decimal,InvalidOperation
from flask import Blueprint,abort,flash,redirect,render_template,request,url_for
from flask_login import current_user,login_required
from app import db
from app.models import Product,Sale
sales_bp=Blueprint("sales",__name__,url_prefix="/sales")
def business(): return current_user.businesses[0]
@sales_bp.route("/",methods=["GET","POST"])
@login_required
def index():
 b=business(); products=Product.query.filter_by(business_id=b.id).order_by(Product.name).all()
 if request.method=="POST":
  try: pid=int(request.form["product_id"]); qty=int(request.form["quantity"]); price=Decimal(request.form.get("unit_price") or "0"); sold_at=datetime.strptime(request.form.get("sold_at",""),"%Y-%m-%d") if request.form.get("sold_at") else datetime.utcnow()
  except (ValueError,InvalidOperation,KeyError): flash("Enter valid sale details.","error"); return redirect(url_for("sales.index"))
  p=db.session.get(Product,pid)
  if not p or p.business_id!=b.id: abort(404)
  if qty<1: flash("Quantity must be at least 1.","error")
  elif qty>p.stock_quantity: flash(f"Only {p.stock_quantity} units of {p.name} are available.","error")
  elif price<0: flash("Price cannot be negative.","error")
  else:
   price=p.selling_price if price==0 else price
   db.session.add(Sale(business_id=b.id,product_id=p.id,quantity=qty,unit_price=price,unit_cost=p.buying_price,sold_at=sold_at)); p.stock_quantity-=qty; db.session.commit(); flash("Sale recorded and stock updated.","success"); return redirect(url_for("sales.index"))
 sales=Sale.query.filter_by(business_id=b.id).order_by(Sale.sold_at.desc()).all()
 return render_template("sales/index.html",business=b,products=products,sales=sales,total=sum(s.total for s in sales),profit=sum(s.profit for s in sales))
@sales_bp.post("/<int:sale_id>/delete")
@login_required
def delete(sale_id):
 b=business(); sale=db.session.get(Sale,sale_id)
 if not sale or sale.business_id!=b.id: abort(404)
 sale.product.stock_quantity+=sale.quantity; db.session.delete(sale); db.session.commit(); flash("Sale removed and stock restored.","success"); return redirect(url_for("sales.index"))
