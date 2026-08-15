import os
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename
from datetime import datetime, date, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, session, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from authlib.integrations.flask_client import OAuth
from flask_mail import Mail, Message as MailMessage
from apscheduler.schedulers.background import BackgroundScheduler

# ตั้งค่าให้ OAuth ยอมรับ HTTP (สำหรับ dev/IP)
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.config['SECRET_KEY'] = 'super-secret-jodya-key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///jodya.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'static/uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# ==========================================
# ตั้งค่า Email & Google OAuth
# ==========================================
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')

GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET')

db = SQLAlchemy(app)
mail = Mail(app)
oauth = OAuth(app)

google = oauth.register(
    name='google',
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    access_token_url='https://accounts.google.com/o/oauth2/token',
    authorize_url='https://accounts.google.com/o/oauth2/auth',
    api_base_url='https://www.googleapis.com/oauth2/v1/',
    jwks_uri='https://www.googleapis.com/oauth2/v3/certs',
    client_kwargs={'scope': 'openid email profile'},
)

login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

# ==========================================
# Database Models (ตัด role และ doctor ออก)
# ==========================================
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    google_id = db.Column(db.String(100), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    name = db.Column(db.String(150), nullable=False)

class Medication(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    med_name = db.Column(db.String(100), nullable=False)
    dosage = db.Column(db.String(50)) 
    instruction = db.Column(db.String(100)) 
    time_to_take = db.Column(db.String(10)) 
    image_file = db.Column(db.String(255), nullable=True)
    total_pills = db.Column(db.Integer, default=10)
    duration_days = db.Column(db.Integer, default=5)
    start_date = db.Column(db.Date, default=date.today)
    
    patient = db.relationship('User', foreign_keys=[patient_id])

class MedLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    med_id = db.Column(db.Integer, db.ForeignKey('medication.id'))
    date_logged = db.Column(db.Date, default=datetime.utcnow().date)
    status = db.Column(db.String(20), default='pending') 

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ==========================================
# Routes
# ==========================================
@app.route('/')
@login_required
def index():
    # โหลดเฉพาะยาของคนที่ล็อกอินอยู่เข้ามาแสดง
    meds = Medication.query.filter_by(patient_id=current_user.id).all()
    today = datetime.now().date()
    logs = {log.med_id: log.status for log in MedLog.query.filter_by(date_logged=today).all()}
    return render_template('patient_dash.html', meds=meds, logs=logs)

@app.route('/login')
def login():
    return render_template('login.html')

@app.route('/login/<role>')
def login_role(role):
    session['temp_role'] = role
    return google.authorize_redirect(url_for('authorize', _external=True), prompt='select_account')

@app.route('/authorize')
def authorize():
    token = google.authorize_access_token()
    user_info = google.get('userinfo').json()
    
    user = User.query.filter_by(email=user_info['email']).first()
    if not user:
        user = User(google_id=user_info['id'], email=user_info['email'], name=user_info['name'])
        db.session.add(user)
        db.session.commit()
        
    login_user(user)
    return redirect(url_for('index'))

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# ==========================================
# จัดการเพิ่มยา (คนไข้เพิ่มเอง)
# ==========================================
@app.route('/add_med', methods=['POST'])
@login_required
def add_med():
    med_name = request.form.get('med_name')
    dosage = request.form.get('dosage')
    instruction = request.form.get('instruction')
    time_to_take = request.form.get('time_to_take')
    total_pills = int(request.form.get('total_pills', 10))
    duration_days = int(request.form.get('duration_days', 5))
    
    image_file = None
    if 'med_image' in request.files:
        file = request.files['med_image']
        if file.filename != '':
            filename = secure_filename(file.filename)
            filename = f"{int(datetime.utcnow().timestamp())}_{filename}"
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            image_file = filename
            
    new_med = Medication(
        patient_id=current_user.id, # บันทึกเข้าไอดีคนไข้ที่กำลังล็อกอิน
        med_name=med_name,
        dosage=dosage,
        instruction=instruction,
        time_to_take=time_to_take,
        total_pills=total_pills,
        duration_days=duration_days,
        start_date=date.today(),
        image_file=image_file
    )
    
    db.session.add(new_med)
    db.session.commit()
    flash('บันทึกรายการยาและตั้งเวลาแจ้งเตือนเรียบร้อย!', 'success')
    return redirect(url_for('index'))

@app.route('/take_med/<int:med_id>')
@login_required
def take_med(med_id):
    med = Medication.query.get_or_404(med_id)
    if med.patient_id != current_user.id:
        abort(403)
        
    today = datetime.now().date()
    log = MedLog.query.filter_by(med_id=med.id, date_logged=today).first()
    
    if not log:
        log = MedLog(med_id=med.id, date_logged=today, status='taken')
        db.session.add(log)
    else:
        log.status = 'taken'
        
    db.session.commit()
    flash(f'บันทึกการกินยา {med.med_name} เรียบร้อย เก่งมากครับ!', 'success')
    return redirect(url_for('index'))

# ==========================================
# Background Scheduler (แจ้งเตือนอีเมล 5 วันอัตโนมัติ)
# ==========================================
def check_medication_reminders():
    with app.app_context():
        now = datetime.now()
        current_time_str = now.strftime("%H:%M")
        today = now.date()

        meds = Medication.query.all()
        for med in meds:
            end_date = med.start_date + timedelta(days=med.duration_days)
            if not (med.start_date <= today <= end_date):
                continue # หมดกำหนดวันแล้วหยุดส่งเมล

            if med.time_to_take == current_time_str:
                patient = User.query.get(med.patient_id)
                if patient and patient.email:
                    subject = f"💊 ถึงเวลากินยาแล้ว: {med.med_name}"
                    body = f"สวัสดีคุณ {patient.name}, ถึงเวลากินยา '{med.med_name}' จำนวน {med.dosage} ({med.instruction}) แล้วนะครับ"
                    try:
                        msg = MailMessage(subject, sender=app.config['MAIL_USERNAME'], recipients=[patient.email], body=body)
                        mail.send(msg)
                    except Exception as e:
                        print(f"Mail Error: {e}")

scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(check_medication_reminders, 'interval', minutes=1)
scheduler.start()

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)