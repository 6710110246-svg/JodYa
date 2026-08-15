import os
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, session, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from authlib.integrations.flask_client import OAuth
from flask_mail import Mail, Message as MailMessage
from apscheduler.schedulers.background import BackgroundScheduler
from functools import wraps


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
app.config['MAIL_USERNAME'] = 'YOUR_EMAIL@gmail.com'     # ใส่อีเมลของคุณ
app.config['MAIL_PASSWORD'] = 'YOUR_APP_PASSWORD'        # ใส่ App Password ของอีเมล

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
    access_token_params=None,
    authorize_url='https://accounts.google.com/o/oauth2/auth',
    authorize_params=None,
    api_base_url='https://www.googleapis.com/oauth2/v1/',
    jwks_uri='https://www.googleapis.com/oauth2/v3/certs',
    client_kwargs={'scope': 'openid email profile'},
)

login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

# ==========================================
# Database Models
# ==========================================
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    google_id = db.Column(db.String(100), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    name = db.Column(db.String(150), nullable=False)
    role = db.Column(db.String(20), default='patient') # 'doctor' หรือ 'patient'

class Medication(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    doctor_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    med_name = db.Column(db.String(100), nullable=False)
    dosage = db.Column(db.String(50)) 
    instruction = db.Column(db.String(50)) 
    time_to_take = db.Column(db.String(10)) 
    image_file = db.Column(db.String(255), nullable=True) # เพิ่มบรรทัดนี้
    # เชื่อมความสัมพันธ์เพื่อดึงชื่อคนไข้มาโชว์ง่ายๆ
    patient = db.relationship('User', foreign_keys=[patient_id])

class MedLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    med_id = db.Column(db.Integer, db.ForeignKey('medication.id'))
    date_logged = db.Column(db.Date, default=datetime.utcnow().date)
    status = db.Column(db.String(20), default='pending') 

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def doctor_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if current_user.role != 'doctor':
            abort(403)
        return f(*args, **kwargs)
    return decorated_function

# ==========================================
# Routes (Auth & Dashboard)
# ==========================================
@app.route('/')
@login_required
def index():
    if current_user.role == 'doctor':
        # ดึงรายชื่อคนไข้ทั้งหมดไปแสดงให้หมอเลือกใน Dropdown
        patients = User.query.filter_by(role='patient').all()
        meds = Medication.query.filter_by(doctor_id=current_user.id).all()
        return render_template('doctor_dash.html', patients=patients, meds=meds)
    else:
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
    redirect_uri = url_for('authorize', _external=True)
    # เพิ่ม prompt='select_account' เพื่อบังคับให้ผู้ใช้เลือกอีเมลเสมอ
    return google.authorize_redirect(redirect_uri, prompt='select_account')

@app.route('/authorize')
def authorize():
    token = google.authorize_access_token()
    resp = google.get('userinfo')
    user_info = resp.json()
    
    # เช็คว่ามีอีเมลนี้ในฐานข้อมูลหรือยัง
    user = User.query.filter_by(email=user_info['email']).first()
    
    # ถ้ายืนยันอีเมลครั้งแรก ให้สร้างบัญชีและใส่ role ตามที่กดปุ่มมา
    if not user:
        role = session.get('temp_role', 'patient')
        user = User(google_id=user_info['id'], email=user_info['email'], name=user_info['name'], role=role)
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
# Routes (จัดการยา)
# ==========================================
@app.route('/assign_med', methods=['POST'])
@login_required
@doctor_required
def assign_med():
    patient_id = request.form.get('patient_id')
    med_name = request.form.get('med_name')
    dosage = request.form.get('dosage')
    instruction = request.form.get('instruction')
    time_to_take = request.form.get('time_to_take')
    
    if not patient_id:
        flash('กรุณาเลือกคนไข้ที่ต้องการจ่ายยา', 'danger')
        return redirect(url_for('index'))
        
    # จัดการเซฟไฟล์รูปภาพ (ถ้ามีการอัปโหลด)
    image_file = None
    if 'med_image' in request.files:
        file = request.files['med_image']
        if file.filename != '':
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            image_file = filename
            
    new_med = Medication(
        patient_id=patient_id, doctor_id=current_user.id, 
        med_name=med_name, dosage=dosage, instruction=instruction, 
        time_to_take=time_to_take, image_file=image_file # เพิ่ม image_file ตรงนี้
    )
    
    db.session.add(new_med)
    db.session.commit()
    flash('จ่ายยาให้คนไข้เรียบร้อยแล้ว!', 'success')
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
# Background Scheduler (แจ้งเตือนอีเมล)
# ==========================================
def check_medication_reminders():
    with app.app_context(): 
        now = datetime.now()
        current_time_str = now.strftime("%H:%M")
        time_plus_2_str = (now + timedelta(minutes=2)).strftime("%H:%M")
        today = now.date()

        medications = Medication.query.all()
        for med in medications:
            patient = User.query.get(med.patient_id)
            if not patient or not patient.email: continue
            
            log = MedLog.query.filter_by(med_id=med.id, date_logged=today).first()
            if log and log.status == 'taken':
                continue

            if med.time_to_take == time_plus_2_str:
                subject = f"⏳ เตรียมตัวกินยา: {med.med_name}"
                body = f"สวัสดีคุณ {patient.name}, อีก 2 นาทีจะถึงเวลากินยา {med.med_name} จำนวน {med.dosage} ({med.instruction}) แล้วนะครับ เตรียมยาไว้ได้เลย!"
                mail.send(MailMessage(subject, sender=app.config['MAIL_USERNAME'], recipients=[patient.email], body=body))

            elif med.time_to_take == current_time_str:
                subject = f"💊 ถึงเวลากินยาแล้ว!: {med.med_name}"
                body = f"ถึงเวลากินยา {med.med_name} แล้วครับ! กินเสร็จแล้วอย่าลืมเข้ามาที่เว็บ JodYa เพื่อกดยืนยันด้วยนะครับ"
                mail.send(MailMessage(subject, sender=app.config['MAIL_USERNAME'], recipients=[patient.email], body=body))

scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(check_medication_reminders, 'interval', minutes=1)
scheduler.start()

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)