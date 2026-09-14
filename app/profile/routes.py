from flask import Blueprint,flash,redirect,render_template,request,url_for
from flask_login import current_user,login_required
from app import db
profile_bp=Blueprint("profile",__name__,url_prefix="/profile")
@profile_bp.route("/",methods=["GET","POST"])
@login_required
def index():
 b=current_user.businesses[0]
 if request.method=="POST":
  name=request.form.get("full_name","").strip(); business_name=request.form.get("business_name","").strip()
  if not name or not business_name: flash("Your name and business name are required.","error")
  else: current_user.full_name=name; b.name=business_name; db.session.commit(); flash("Profile updated.","success"); return redirect(url_for("profile.index"))
 return render_template("profile/index.html",business=b)
