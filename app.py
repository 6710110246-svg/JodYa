import os
import time  # 👈 เติมบรรทัดนี้ลงไปครับ!
import re
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename
from datetime import datetime, date, timedelta ,timezone
from flask import Flask, render_template, request, redirect, url_for, flash, session, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from authlib.integrations.flask_client import OAuth
from flask_mail import Mail, Message as MailMessage
from apscheduler.schedulers.background import BackgroundScheduler

os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
# ตั้งค่า Timezone เป็นเวลาไทย
os.environ['TZ'] = 'Asia/Bangkok'
if hasattr(time, 'tzset'):
    time.tzset()

THAILAND_TZ = timezone(timedelta(hours=7))
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.config['SECRET_KEY'] = 'super-secret-jodya-key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///jodya.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'static/uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

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
# Database Models
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
    image_file = db.Column(db.String(255), nullable=True)
    total_pills = db.Column(db.Integer, default=10)
    duration_days = db.Column(db.Integer, default=5)
    start_date = db.Column(db.Date, default=date.today)
    
    patient = db.relationship('User', foreign_keys=[patient_id])
    times = db.relationship('MedicationTime', backref='medication', cascade='all, delete-orphan')

class MedicationTime(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    medication_id = db.Column(db.Integer, db.ForeignKey('medication.id'), nullable=False)
    time_to_take = db.Column(db.String(10), nullable=False)

class MedLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    med_id = db.Column(db.Integer, db.ForeignKey('medication.id'))
    date_logged = db.Column(db.Date, default=datetime.utcnow().date)
    status = db.Column(db.String(20), default='pending') 
    feeling = db.Column(db.String(50), nullable=True)  # เพิ่ม: สำหรับเก็บ Emoji อาการ
    note = db.Column(db.Text, nullable=True)
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ==========================================
# Routes
# ==========================================
@app.route('/')
@login_required
def index():
    meds = Medication.query.filter_by(patient_id=current_user.id).all()
    today = datetime.now().date()
    logs = {log.med_id: log.status for log in MedLog.query.filter_by(date_logged=today).all()}
    return render_template('patient_dash.html', meds=meds, logs=logs)

@app.route('/login')
def login():
    return render_template('login.html')

@app.route('/login/<role>')
def login_role(role='patient'):
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

@app.route('/manual')
@login_required
def manual():
    return render_template('manual.html')

@app.route('/add_med', methods=['POST'])
@login_required
def add_med():
    med_name = request.form.get('med_name')
    dosage = request.form.get('dosage')
    instruction = request.form.get('instruction')
    total_pills = int(request.form.get('total_pills', 10))
    duration_days = int(request.form.get('duration_days', 5))
    
    # รับค่าเวลาหลายๆ เวลาที่ส่งมาจากฟอร์ม
    times_list = request.form.getlist('times_to_take[]')
    
    image_file = None
    if 'med_image' in request.files:
        file = request.files['med_image']
        if file and file.filename != '':
            filename = secure_filename(file.filename)
            filename = f"{int(datetime.utcnow().timestamp())}_{filename}"
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            image_file = filename
            
    new_med = Medication(
        patient_id=current_user.id,
        med_name=med_name,
        dosage=dosage,
        instruction=instruction,
        total_pills=total_pills,
        duration_days=duration_days,
        start_date=date.today(),
        image_file=image_file
    )
    
    db.session.add(new_med)
    db.session.commit()

    # บันทึกเวลาทั้งหมดลงตารางย่อย
    for t in times_list:
        if t.strip():
            med_time = MedicationTime(medication_id=new_med.id, time_to_take=t.strip())
            db.session.add(med_time)
    db.session.commit()

    flash('บันทึกรายการยาและตั้งเวลาแจ้งเตือนเรียบร้อย!', 'success')
    return redirect(url_for('index'))

@app.route('/delete_med/<int:med_id>')
@login_required
def delete_med(med_id):
    med = Medication.query.get_or_404(med_id)
    if med.patient_id != current_user.id:
        abort(403)
    db.session.delete(med)
    db.session.commit()
    flash('ลบรายการยาเรียบร้อยแล้ว', 'success')
    return redirect(url_for('index'))

@app.route('/take_med/<int:med_id>', methods=['GET', 'POST'])
@login_required
def take_med(med_id):
    med = Medication.query.get_or_404(med_id)
    if med.patient_id != current_user.id:
        abort(403)
        
    today = datetime.now(THAILAND_TZ).date()
    log = MedLog.query.filter_by(med_id=med.id, date_logged=today).first()
    
    feeling = None
    note = None
    
    # ถ้ารับข้อมูลมาจากหน้าต่าง Pop-up
    if request.method == 'POST':
        feeling = request.form.get('feeling')
        note = request.form.get('note')

    # บันทึกข้อมูลลงฐานข้อมูล
    if not log:
        log = MedLog(med_id=med.id, date_logged=today, status='taken', feeling=feeling, note=note)
        db.session.add(log)
    else:
        log.status = 'taken'
        if request.method == 'POST':
            log.feeling = feeling
            log.note = note
            
    db.session.commit()
    flash(f'บันทึกการกินยา {med.med_name} และอาการเรียบร้อยครับ!', 'success')
    return redirect(url_for('index'))

@app.route('/test-mail')
@login_required
def test_mail():
    try:
        msg = MailMessage(
            subject="🧪 ทดสอบระบบส่งเมล JodYa",
            sender=app.config['MAIL_USERNAME'],
            recipients=[current_user.email],
            body="ถ้าคุณได้รับเมลนี้ แสดงว่าระบบส่งเมลทำงานปกติ 100% แล้วครับ!"
        )
        mail.send(msg)
        return "ส่งเมลทดสอบสำเร็จ! ลองเช็กใน Inbox หรือ Spam ดูครับ"
    except Exception as e:
        return f"ส่งเมลไม่สำเร็จ เกิดข้อผิดพลาด: {e}"

# ==========================================
# Background Scheduler (แจ้งเตือน ลดเม็ด, และลดวันทุกเที่ยงคืน)
# ==========================================
last_day_checked = None

def check_medication_reminders():
    global last_day_checked
    import re  # นำเข้าโมดูลสำหรับดึงตัวเลขจากข้อความ (ใช้แปลงคำว่า "1 เม็ด" ให้เป็นเลข 1)
    
    with app.app_context():
        now = datetime.now()
        current_time_str = now.strftime("%H:%M")
        today = now.date()

        # 1. ระบบลดจำนวนวันลง 1 วัน เมื่อผ่านพ้นเที่ยงคืน (ขึ้นวันใหม่)
        if last_day_checked != today:
            all_meds = Medication.query.all()
            for m in all_meds:
                if m.duration_days > 0:
                    m.duration_days -= 1
            db.session.commit()
            last_day_checked = today

        # 2. ตรวจสอบเวลาแจ้งเตือนเพื่อส่งเมลและหักจำนวนยา
        med_times = MedicationTime.query.all()
        for mt in med_times:
            med = Medication.query.get(mt.medication_id)
            if not med:
                continue
            
            # ลูปจะหยุดทำงานทันที ถ้ายาหมด (0 เม็ด) หรือจำนวนวันหมด
            if med.total_pills <= 0 or med.duration_days < 0:
                continue

            # จัด Format เวลาให้อยู่ในรูปแบบ HH:MM เสมอ
            db_time = mt.time_to_take.strip()
            try:
                parsed_time = datetime.strptime(db_time, "%H:%M").strftime("%H:%M")
            except ValueError:
                parsed_time = db_time

            # 3. เมื่อเวลาปัจจุบัน ตรงกับ เวลาที่ตั้งไว้
            if parsed_time == current_time_str:
                patient = User.query.get(med.patient_id)
                if patient and patient.email:
                    
                    # ค้นหาตัวเลขจากช่อง "ปริมาณต่อมื้อ" (เช่น พิมพ์ "1 เม็ด" หรือ "1" ระบบจะดึงเลข 1 มาใช้)
                    deduct_amount = 1
                    match = re.search(r'\d+', str(med.dosage))
                    if match:
                        deduct_amount = int(match.group())

                    # ทำการลบจำนวนยาออกตามที่ทานไป
                    if med.total_pills > 0:
                        med.total_pills -= deduct_amount
                        # ป้องกันกรณียาติดลบ (เช่น ยาเหลือ 1 แต่ต้องกิน 2)
                        if med.total_pills < 0:
                            med.total_pills = 0 
                        db.session.commit()

                    # ส่งอีเมลแจ้งเตือน พร้อมรายงานสถานะยาคงเหลือล่าสุด
                    subject = f"💊 ถึงเวลากินยาแล้ว: {med.med_name}"
                    body = f"สวัสดีคุณ {patient.name},\n\nถึงเวลากินยา '{med.med_name}' จำนวน {med.dosage} ({med.instruction}) แล้วนะครับ\n\nสถานะยาปัจจุบัน:\n- ยาคงเหลือ: {med.total_pills} เม็ด\n- ทานต่อเนื่องอีก: {med.duration_days} วัน\n\nอย่าลืมทานยานะครับ!"
                    
                    try:
                        msg = MailMessage(subject, sender=app.config['MAIL_USERNAME'], recipients=[patient.email], body=body)
                        mail.send(msg)
                        print(f"Email sent successfully. Remaining pills: {med.total_pills}")
                    except Exception as e:
                        print(f"Mail Error: {e}")
scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(check_medication_reminders, 'interval', minutes=1)
scheduler.start()

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)