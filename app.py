import os
import uuid
import sys
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from authlib.integrations.flask_client import OAuth
from flask_mail import Mail, Message as MailMessage
import threading 

# ตั้งค่าให้ OAuth ยอมรับ HTTP (สำหรับ dev/IP)
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

app = Flask(__name__)
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'phatwp1411@gmail.com' 
app.config['MAIL_PASSWORD'] = 'chri unss wwtc lost'   

mail = Mail(app)

def send_async_email(app, msg):
    with app.app_context():
        try:
            mail.send(msg)
        except Exception as e:
            print(f"🔴 Email Error: {e}")

def send_notification_email(to_email, subject, body):
    if not to_email:
        return
    msg = MailMessage(subject, sender=app.config['MAIL_USERNAME'], recipients=[to_email])
    msg.body = body
    threading.Thread(target=send_async_email, args=(app, msg)).start()
app.secret_key = 'super_secret_key'
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Google OAuth Config
app.config['GOOGLE_CLIENT_ID'] = '88358153370-5et4fcenvknbsp1gffemkim8qkloo968.apps.googleusercontent.com'
app.config['GOOGLE_CLIENT_SECRET'] = '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'

oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=app.config['GOOGLE_CLIENT_ID'],
    client_secret=app.config['GOOGLE_CLIENT_SECRET'],
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'mov', 'avi', 'webm', 'mkv'}

# --- Models ---
followers = db.Table('followers',
    db.Column('follower_id', db.Integer, db.ForeignKey('user.id')),
    db.Column('followed_id', db.Integer, db.ForeignKey('user.id'))
)

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    message = db.Column(db.String(255), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False)
    sender = db.relationship('User', foreign_keys=[sender_id])

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)
    profile_image = db.Column(db.String(100), nullable=True, default='default.png')
    email = db.Column(db.String(150), unique=True, nullable=True)
    google_id = db.Column(db.String(100), unique=True, nullable=True)
    
    followed = db.relationship(
        'User', secondary=followers,
        primaryjoin=(followers.c.follower_id == id),
        secondaryjoin=(followers.c.followed_id == id),
        backref=db.backref('followers', lazy='dynamic'), lazy='dynamic'
    )
    
    notifications = db.relationship('Notification', foreign_keys=[Notification.user_id], backref='user', lazy='dynamic')

    def is_following(self, user):
        return self.followed.filter(followers.c.followed_id == user.id).count() > 0

    def follow(self, user):
        if not self.is_following(user):
            self.followed.append(user)
            notif = Notification(user_id=user.id, sender_id=self.id, message="เริ่มติดตามคุณ")
            db.session.add(notif)

    def unfollow(self, user):
        if self.is_following(user):
            self.followed.remove(user)
    def is_following_by_name(self, username):
        user = User.query.filter_by(username=username).first()
        if user:
            return self.is_following(user)
        return False
def is_friend(user_a, user_b):
    return user_a.is_following(user_b) and user_b.is_following(user_a)

class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    author = db.Column(db.String(100), nullable=False)
    media_list = db.Column(db.Text, nullable=True)
    timestamp = db.Column(db.String(50), nullable=False)
    # [NEW] เก็บ ID ของโพสต์ต้นฉบับ (ถ้าเป็นโพสต์ธรรมดาจะเป็น Null)
    original_post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=True)
    # [NEW] สร้างความสัมพันธ์เพื่อให้ดึงข้อมูลต้นฉบับได้ง่ายๆ (post.original_post)
    original_post = db.relationship('Post', remote_side=[id], backref='shares')

class Like(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))

class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'))
    user = db.Column(db.String(100))
    content = db.Column(db.Text)
    timestamp = db.Column(db.String(50))

class ChatRoom(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user1_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    user2_id = db.Column(db.Integer, db.ForeignKey('user.id'))

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey('chat_room.id'))
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    content = db.Column(db.Text, nullable=True)
    media_path = db.Column(db.String(255), nullable=True)
    media_type = db.Column(db.String(10), default='text')
    is_read = db.Column(db.Boolean, default=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

with app.app_context():
    db.create_all()

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_processed_posts(query=None):
    if query:
        posts = Post.query.filter(
            (Post.title.contains(query)) | 
            (Post.content.contains(query)) | 
            (Post.author.contains(query))
        ).order_by(Post.id.desc()).all()
    else:
        posts = Post.query.order_by(Post.id.desc()).all()

    VIDEO_EXTS = {'mp4', 'mov', 'avi', 'webm', 'mkv'}
    for post in posts:
        post.struct_media = []
        if post.media_list:
            paths = post.media_list.split(',')
            for p in paths:
                p = p.strip()
                if not p: continue
                try:
                    ext = p.split('.')[-1].lower()
                except:
                    ext = ""
                m_type = 'video' if ext in VIDEO_EXTS else 'image'
                post.struct_media.append({'path': p, 'type': m_type})
    return posts

@app.context_processor
def inject_user_image():
    def get_profile_image(username):
        user = User.query.filter_by(username=username).first()
        if user and user.profile_image:
            return url_for('static', filename='profile_pics/' + user.profile_image)
        return url_for('static', filename='profile_pics/default.png') 
    return dict(get_profile_image=get_profile_image)

# --- Routes ---

@app.route('/')
def index():
    query = request.args.get('q')
    posts = get_processed_posts(query)
    for post in posts:
        post.like_count = Like.query.filter_by(post_id=post.id).count()
        post.is_liked = False
        if current_user.is_authenticated:
            post.is_liked = Like.query.filter_by(post_id=post.id, user_id=current_user.id).first() is not None
        post.comments = Comment.query.filter_by(post_id=post.id).all()
    return render_template('index.html', posts=posts)

@app.route('/feed')
def feed_content():
    query = request.args.get('q')
    posts = get_processed_posts(query)
    for post in posts:
        post.like_count = Like.query.filter_by(post_id=post.id).count()
        post.is_liked = False
        if current_user.is_authenticated:
            post.is_liked = Like.query.filter_by(post_id=post.id, user_id=current_user.id).first() is not None
        post.comments = Comment.query.filter_by(post_id=post.id).all()
    return render_template('feed.html', posts=posts)

@app.route('/like/<int:post_id>')
@login_required
def like_post(post_id):
    post = Post.query.get_or_404(post_id)
    like = Like.query.filter_by(post_id=post_id, user_id=current_user.id).first()
    
    if like:
        db.session.delete(like)
    else:
        db.session.add(Like(post_id=post_id, user_id=current_user.id))
        
        if post.author != current_user.username:
            author = User.query.filter_by(username=post.author).first()
            if author:
                notif = Notification(
                    user_id=author.id, 
                    sender_id=current_user.id, 
                    message="ถูกใจโพสต์ของคุณ"
                )
                db.session.add(notif)

                send_notification_email(
                    author.email,
                    "มีคนถูกใจโพสต์ของคุณ",
                    f"{current_user.username} ถูกใจโพสต์ของคุณ")

    db.session.commit()
    return redirect(url_for('index') + f"#comments-{post_id}")

@app.route('/comment/<int:post_id>', methods=['POST'])
@login_required
def comment_post(post_id):
    content = request.form['content']
    thai_time = (datetime.utcnow() + timedelta(hours=7)).strftime("%d/%m/%Y %H:%M")
    
    c = Comment(post_id=post_id, user=current_user.username, content=content, timestamp=thai_time)
    db.session.add(c)
    
    post = Post.query.get(post_id)
    if post.author != current_user.username:
        author = User.query.filter_by(username=post.author).first()
        if author:
            notif = Notification(
                user_id=author.id, 
                sender_id=current_user.id, 
                message=f"แสดงความคิดเห็น: {content[:20]}..."
            )
            db.session.add(notif)

            send_notification_email(
                author.email,
                "มีความคิดเห็นใหม่ในโพสต์ของคุณ",
                f"{current_user.username} แสดงความคิดเห็น: \"{content}\""
            )

    db.session.commit()
    return redirect(url_for('index') + f"#comments-{post_id}")

@app.route('/api/notif_count')
@login_required
def get_notif_count():
    count = current_user.notifications.filter_by(is_read=False).count()
    return jsonify({'count': count})

@app.route('/follow/<username>')
@login_required
def follow(username):
    user_to_follow = User.query.filter_by(username=username).first()
    if user_to_follow and user_to_follow != current_user:
        current_user.follow(user_to_follow)
        db.session.commit()
        
        send_notification_email(
            user_to_follow.email, 
            "มีคนติดตามคุณใหม่!", 
            f"{current_user.username} ได้เริ่มติดตามคุณ"
        )
        
        flash(f'ติดตาม {username} แล้ว!')
    return redirect(request.referrer or url_for('index'))

@app.route('/unfollow/<username>')
@login_required
def unfollow(username):
    user_to_unfollow = User.query.filter_by(username=username).first()
    if user_to_unfollow:
        current_user.unfollow(user_to_unfollow)
        db.session.commit()
        flash(f'เลิกติดตาม {username} แล้ว')
    return redirect(request.referrer or url_for('index'))

@app.route('/following')
@login_required
def following_page():
    following_list = current_user.followed.all()
    return render_template('following.html', following_list=following_list)

@app.route('/notifications')
@login_required
def notifications():
    notifs = current_user.notifications.order_by(Notification.timestamp.desc()).all()
    for n in notifs:
        if not n.is_read: n.is_read = True
    db.session.commit()
    return render_template('notifications.html', notifs=notifs)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if User.query.filter_by(username=username).first():
            flash('ชื่อซ้ำครับ')
            return redirect(url_for('register'))
        hashed_pw = generate_password_hash(password, method='scrypt')
        new_user = User(username=username, password=hashed_pw)
        db.session.add(new_user)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/chats')
@login_required
def chat_list():
    chats = []
    friends = [u for u in current_user.followed.all() if u.is_following(current_user)]
    for friend in friends:
        room = ChatRoom.query.filter(
            ((ChatRoom.user1_id == current_user.id) & (ChatRoom.user2_id == friend.id)) |
            ((ChatRoom.user1_id == friend.id) & (ChatRoom.user2_id == current_user.id))
        ).first()
        last_message = None
        unread_count = 0
        last_time = ""
        if room:
            last_message = Message.query.filter_by(room_id=room.id).order_by(Message.timestamp.desc()).first()
            unread_count = Message.query.filter_by(room_id=room.id, sender_id=friend.id, is_read=False).count()
            if last_message:
                local_time = last_message.timestamp + timedelta(hours=7)
                last_time = local_time.strftime('%H:%M')
        chats.append({
            "user": friend,
            "last_message": last_message,
            "unread_count": unread_count,
            "last_time": last_time
        })
    return render_template("chat_list.html", chats=chats)

@app.route('/chat/<username>', methods=['GET', 'POST'])
@login_required
def chat_room(username):
    other = User.query.filter_by(username=username).first_or_404()
    if not is_friend(current_user, other):
        flash('ต้องเป็นเพื่อนกันก่อนถึงจะคุยได้')
        return redirect(url_for('chat_list'))

    room = ChatRoom.query.filter(
        ((ChatRoom.user1_id == current_user.id) & (ChatRoom.user2_id == other.id)) |
        ((ChatRoom.user1_id == other.id) & (ChatRoom.user2_id == current_user.id))
    ).first()

    if not room:
        room = ChatRoom(user1_id=current_user.id, user2_id=other.id)
        db.session.add(room)
        db.session.commit()

    if request.method == "POST":
        content = ""
        media_path = None
        media_type = 'text'
        
        if 'file' in request.files:
            file = request.files['file']
            content = request.form.get('message', '')
            if file and allowed_file(file.filename):
                ext = file.filename.split('.')[-1].lower()
                filename = f"chat_{uuid.uuid4().hex}.{ext}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                media_path = f"uploads/{filename}"
                if ext in {'mp4', 'mov', 'avi', 'webm', 'mkv'}: media_type = 'video'
                else: media_type = 'image'
        
        elif request.is_json:
            data = request.get_json()
            content = data.get("message")
        
        if content or media_path:
            msg = Message(room_id=room.id, sender_id=current_user.id, content=content, media_path=media_path, media_type=media_type)
            db.session.add(msg)
            
            if other.id != current_user.id:
                notif = Notification(
                    user_id=other.id,
                    sender_id=current_user.id,
                    message="ส่งข้อความหาคุณ"
                )
                db.session.add(notif)

                send_notification_email(
                    other.email, 
                    "ข้อความใหม่จากเพื่อน", 
                    f"{current_user.username} ส่งข้อความหาคุณ: \"{content[:30] if content else 'ส่งไฟล์แนบ'}\""
                )

            db.session.commit()
            return jsonify(success=True)

    return render_template('chat_room.html', other=other)

@app.route('/chat_messages/<username>')
@login_required
def chat_messages(username):
    other = User.query.filter_by(username=username).first_or_404()
    room = ChatRoom.query.filter(
        ((ChatRoom.user1_id == current_user.id) & (ChatRoom.user2_id == other.id)) |
        ((ChatRoom.user1_id == other.id) & (ChatRoom.user2_id == current_user.id))
    ).first()
    if not room: return jsonify([])

    unread_msgs = Message.query.filter_by(room_id=room.id, sender_id=other.id, is_read=False).all()
    if unread_msgs:
        for msg in unread_msgs: msg.is_read = True
        db.session.commit()

    messages = Message.query.filter_by(room_id=room.id).order_by(Message.timestamp).all()
    results = []
    for m in messages:
        local_time = m.timestamp + timedelta(hours=7)
        results.append({
            "id": m.id,
            "sender_id": m.sender_id,
            "content": m.content,
            "media_path": m.media_path,
            "media_type": m.media_type,
            "time": local_time.strftime('%H:%M'),
            "is_read": m.is_read
        })
    return jsonify(results)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('index'))
        else: flash('Login Failed')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/create', methods=['GET', 'POST'])
@login_required
def create_post():
    if request.method == 'POST':
        title = request.form['title']
        content = request.form['content']
        media_paths = []
        
        if 'file' in request.files:
            files = request.files.getlist('file')
            for file in files:
                if file and allowed_file(file.filename):
                    ext = os.path.splitext(file.filename)[1].lower()
                    new_filename = f"{uuid.uuid4().hex}{ext}"
                    file.save(os.path.join(app.config['UPLOAD_FOLDER'], new_filename))
                    media_paths.append(f'uploads/{new_filename}')
        
        thai_time = (datetime.utcnow() + timedelta(hours=7)).strftime("%d/%m/%Y %H:%M")
        
        new_post = Post(title=title, content=content, author=current_user.username, media_list=",".join(media_paths), timestamp=thai_time)
        db.session.add(new_post)
        
        followers = current_user.followers.all() 
        for follower in followers:
            notif = Notification(
                user_id=follower.id,
                sender_id=current_user.id,
                message=f"โพสต์ใหม่: {title}"
            )
            db.session.add(notif)
            
            send_notification_email(
                follower.email,
                f"{current_user.username} โพสต์ใหม่",
                f"{current_user.username} ได้โพสต์เรื่องใหม่: \"{title}\"\n\nไปดูเลย!")

        db.session.commit()
        return redirect(url_for('index'))
    return render_template('create.html')

@app.route('/status')
def status_check():
    return redirect("https://check-status-final-88358153370.asia-southeast1.run.app")

@app.route('/profile/<username>', methods=['GET', 'POST'])
@login_required
def profile(username):
    user = User.query.filter_by(username=username).first_or_404()
    
    if request.method == 'POST' and current_user.username == username:
        
        new_username = request.form.get('new_username')
        if new_username and new_username != current_user.username:
            if User.query.filter_by(username=new_username).first():
                flash('ชื่อนี้มีคนใช้แล้ว กรุณาใช้ชื่ออื่น')
                return redirect(url_for('profile', username=username))
            
            old_username = current_user.username
            current_user.username = new_username
            
            Post.query.filter_by(author=old_username).update({'author': new_username})
            Comment.query.filter_by(user=old_username).update({'user': new_username})
            
            db.session.commit()
            flash('เปลี่ยนชื่อเรียบร้อยแล้ว!')
            username = new_username

        if 'profile_pic' in request.files:
            file = request.files['profile_pic']
            if file and allowed_file(file.filename):
                ext = file.filename.split('.')[-1].lower()
                filename = f"{username}_{uuid.uuid4().hex[:8]}.{ext}"
                
                pic_path = os.path.join(app.root_path, 'static', 'profile_pics')
                
                os.makedirs(pic_path, exist_ok=True) 
                file.save(os.path.join(pic_path, filename))
                
                current_user.profile_image = filename
                db.session.commit()
                
        return redirect(url_for('profile', username=username))

    posts = Post.query.filter_by(author=user.username).order_by(Post.id.desc()).all()
    for post in posts:
        post.filename = None
        if post.media_list:
            post.filename = post.media_list.split(',')[0].strip().replace('uploads/', '')

    return render_template('profile.html', user=user, posts=posts, post_count=len(posts),
                        follower_count=user.followers.count(), following_count=user.followed.count(),
                        following_users=user.followed.all())

@app.route('/delete/<int:post_id>', methods=['POST'])
@login_required
def delete_post(post_id):
    post = Post.query.get_or_404(post_id)
    if post.author == current_user.username:
        if post.media_list:
            for p in post.media_list.split(','):
                try: os.remove(os.path.join(app.root_path, 'static', p.strip()))
                except: pass
        Like.query.filter_by(post_id=post.id).delete()
        Comment.query.filter_by(post_id=post.id).delete()
        db.session.delete(post)
        db.session.commit()
    return redirect(request.referrer or url_for('index'))

@app.route('/login/google')
def login_google():
    redirect_uri = url_for('google_callback', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/login/google/callback')
def google_callback():
    token = google.authorize_access_token()
    user_info = google.get('https://www.googleapis.com/oauth2/v3/userinfo').json()
    email = user_info['email']
    user = User.query.filter((User.google_id == user_info['sub']) | (User.email == email)).first()
    if not user:
        user = User(username=user_info['name'].replace(" ", ""), password=generate_password_hash(uuid.uuid4().hex), email=email, google_id=user_info['sub'])
        db.session.add(user)
        db.session.commit()
    login_user(user)
    return redirect(url_for('index'))

@app.route('/search_users')
@login_required
def search_users():
    query = request.args.get('q', '').strip()
    users = []
    if query:
        users = User.query.filter(User.username.ilike(f'%{query}%'), User.id != current_user.id).all()
    else:
        users = User.query.filter(User.id != current_user.id).order_by(User.id.desc()).limit(10).all()
    return render_template('search.html', users=users, query=query)

# --- Following Feed Route ---
@app.route('/feed/following')
@login_required
def following_feed():
    # 1. หา ID ของคนที่เราติดตามทั้งหมด
    followed_users_ids = [user.id for user in current_user.followed.all()]
    
    # 2. ถ้าไม่ได้ติดตามใครเลย ให้ส่งลิสต์ว่างๆ ไป (หรือจะส่งโพสต์แนะนำก็ได้)
    if not followed_users_ids:
        posts = []
    else:
        # 3. ดึงโพสต์ที่มี author เป็นคนที่เราติดตาม
        # หมายเหตุ: เนื่องจากใน Post เราเก็บ author เป็นชื่อ (String) แต่ followed_users_ids เป็น ID (Integer)
        # เราจึงต้องดึงชื่อของคนที่เราติดตามมาก่อน
        followed_usernames = [user.username for user in current_user.followed.all()]
        
        posts = Post.query.filter(Post.author.in_(followed_usernames)).order_by(Post.id.desc()).all()

    # 4. ประมวลผลโพสต์ (Like, Comment, Media) เหมือนหน้า Index ปกติ
    VIDEO_EXTS = {'mp4', 'mov', 'avi', 'webm', 'mkv'}
    for post in posts:
        # จัดการ Media List
        post.struct_media = []
        if post.media_list:
            paths = post.media_list.split(',')
            for p in paths:
                p = p.strip()
                if not p: continue
                ext = p.split('.')[-1].lower() if '.' in p else ""
                m_type = 'video' if ext in VIDEO_EXTS else 'image'
                post.struct_media.append({'path': p, 'type': m_type})
        
        # จัดการ Like/Comment
        post.like_count = Like.query.filter_by(post_id=post.id).count()
        post.is_liked = Like.query.filter_by(post_id=post.id, user_id=current_user.id).first() is not None
        post.comments = Comment.query.filter_by(post_id=post.id).all()

    # ใช้ template เดียวกับ index แต่ส่ง posts ที่กรองแล้วไป
    return render_template('index.html', posts=posts, feed_type='following')
@app.route('/share/<int:post_id>', methods=['POST'])
@login_required
def share_post(post_id):

    original = Post.query.get_or_404(post_id)
    
    target_id = original.original_post_id if original.original_post_id else original.id
    target_post = Post.query.get(target_id)
    

    caption = request.form.get('content', '')
    
    thai_time = (datetime.utcnow() + timedelta(hours=7)).strftime("%d/%m/%Y %H:%M")
    
    new_share = Post(
        title=f"Shared post", 
        content=caption,     
        author=current_user.username,
        media_list=None,    
        timestamp=thai_time,
        original_post_id=target_id 
    )
    
    db.session.add(new_share)
    
    if target_post.author != current_user.username:
        target_owner = User.query.filter_by(username=target_post.author).first()
        
        if target_owner:
            notif = Notification(
                user_id=target_owner.id,
                sender_id=current_user.id,
                message=f"ได้แชร์โพสต์ของคุณ"
            )
            db.session.add(notif)
            
            send_notification_email(
                target_owner.email,
                "โพสต์ของคุณถูกแชร์",
                f"{current_user.username} ได้แชร์โพสต์ของคุณ"
            )
            
    db.session.commit()
    flash('แชร์โพสต์เรียบร้อย!')
    return redirect(request.referrer or url_for('index'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True) ##แก้ Port 5000