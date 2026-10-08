from app.subscriptions.entitlements import selected_business
from flask import Blueprint,current_app,flash,redirect,render_template,request,url_for
from flask_login import current_user,login_required
from app import db
from app.models import NotificationPreference
profile_bp=Blueprint("profile",__name__,url_prefix="/profile")
@profile_bp.route("/",methods=["GET","POST"])
@login_required
def index():
 b=selected_business(current_user)
 if request.method=="POST":
  name=request.form.get("full_name","").strip(); business_name=request.form.get("business_name","").strip()
  if not name or not business_name or len(name)>120 or len(business_name)>140: flash("Your name and business name are required.","error")
  elif current_app.config.get("SUBSCRIPTIONS_ENABLED") and not b.has_write_access: flash("Your business is read-only. Manage your plan to edit it.","warning")
  else: current_user.full_name=name; b.name=business_name; db.session.commit(); flash("Profile updated.","success"); return redirect(url_for("profile.index"))
 return render_template("profile/index.html",business=b, preferences=db.session.get(NotificationPreference, b.id))
