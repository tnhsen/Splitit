import os
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from flask.cli import load_dotenv
from pymongo import MongoClient
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from bson.objectid import ObjectId

app = Flask(__name__)
# basedir = os.path.abspath(os.path.dirname(__file__))
# env_path = os.path.join(basedir, '.env')
# load_dotenv(env_path)

app.secret_key = os.getenv('SECRET_KEY', 'default-secret-key')
MONGO_URI = os.getenv('MONGO_URI')
if MONGO_URI is None:
    print("ERROR: หาไฟล์ .env ไม่พบ หรือในไฟล์ไม่มี MONGO_URI")
    
client = MongoClient(MONGO_URI)
db = client['bill_splitter_db']
users_col = db['users']
groups_col = db['groups']
bills_col = db['bills'] 
invitations_col = db['invitations']

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

class User(UserMixin):
    def __init__(self, user_data):
        self.id = str(user_data['_id'])
        self.username = user_data['username']

@login_manager.user_loader
def load_user(user_id):
    user_data = users_col.find_one({"_id": ObjectId(user_id)})
    return User(user_data) if user_data else None

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username, password = request.form.get('username'), request.form.get('password')
        if users_col.find_one({"username": username}):
            flash('ชื่อผู้ใช้นี้มีคนใช้แล้ว')
            return redirect(url_for('signup'))
        users_col.insert_one({"username": username, "password": generate_password_hash(password)})
        flash('สมัครสำเร็จ! เข้าสู่ระบบได้เลย')
        return redirect(url_for('login'))
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username, password = request.form.get('username'), request.form.get('password')
        user_data = users_col.find_one({"username": username})
        if user_data and check_password_hash(user_data['password'], password):
            login_user(User(user_data))
            return redirect(url_for('index'))
        flash('ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    return render_template('index.html', username=current_user.username)

@app.route('/get_groups', methods=['GET'])
@login_required
def get_groups():
    owned = [g['name'] for g in groups_col.find({'owner_id': current_user.id})]
    joined = [i['group_name'] for i in invitations_col.find({'receiver_username': current_user.username, 'status': 'accepted'})]
    return jsonify({'groups': list(set(owned + joined))})

@app.route('/create_group', methods=['POST'])
@login_required
def create_group():
    name = request.json.get('group_name')
    if groups_col.find_one({'name': name, 'owner_id': current_user.id}):
        return jsonify({'status': 'error', 'message': 'ชื่อกลุ่มซ้ำ'})
    groups_col.insert_one({'name': name, 'owner_id': current_user.id, 'created_at': datetime.now()})
    return jsonify({'status': 'success'})

@app.route('/send_invitation', methods=['POST'])
@login_required
def send_invitation():
    data = request.json
    if not users_col.find_one({"username": data['username']}):
        return jsonify({'status': 'error', 'message': 'ไม่พบชื่อผู้ใช้นี้'})
    invitations_col.insert_one({"group_name": data['group_name'], "sender_username": current_user.username, "receiver_username": data['username'], "status": "pending"})
    return jsonify({'status': 'success', 'message': 'ส่งคำเชิญแล้ว'})

@app.route('/get_my_invitations', methods=['GET'])
@login_required
def get_my_invitations():
    invites = list(invitations_col.find({"receiver_username": current_user.username, "status": "pending"}))
    return jsonify({'invitations': [{"id": str(i['_id']), "group_name": i['group_name'], "sender": i['sender_username']} for i in invites]})

@app.route('/respond_invitation', methods=['POST'])
@login_required
def respond_invitation():
    invitations_col.update_one({"_id": ObjectId(request.json['invite_id'])}, {"$set": {"status": request.json['response']}})
    return jsonify({'status': 'success'})

@app.route('/get_group_members/<group_name>', methods=['GET'])
@login_required
def get_group_members(group_name):
    owner = groups_col.find_one({"name": group_name})
    if not owner: return jsonify({'members': []})
    owner_user = users_col.find_one({"_id": ObjectId(owner['owner_id'])})
    accepted = invitations_col.find({"group_name": group_name, "status": "accepted"})
    members = [owner_user['username']] + [i['receiver_username'] for i in accepted]
    return jsonify({'members': list(set(members))})

@app.route('/calculate', methods=['POST'])
@login_required
def calculate():
    data = request.json
    members, total_bill, items, exclude_common, payers_data = data['members'], float(data['total_bill']), data['items'], data['exclude_common'], data['payers']
    owed = {m: 0.0 for m in members}
    total_specific = 0.0
    for item in items:
        price = float(item['price'])
        if item['eaters']:
            share = price / len(item['eaters'])
            for e in item['eaters']: owed[e] += share
            total_specific += price
    common_amount = total_bill - total_specific
    parts = [m for m in members if m not in exclude_common]
    if common_amount > 0 and parts:
        share = common_amount / len(parts)
        for p in parts: owed[p] += share
    paid = {m: 0.0 for m in members}
    for p in payers_data: paid[p['name']] = float(p['amount'])
    balances = {m: paid[m] - owed[m] for m in members}
    creditors = [[n, b] for n, b in balances.items() if b > 0.01]
    debtors = [[n, abs(b)] for n, b in balances.items() if b < -0.01]
    settlements = []
    for d in debtors:
        for c in creditors:
            if d[1] <= 0 or c[1] <= 0: continue
            transfer = min(d[1], c[1])
            settlements.append(f"{d[0]} โอนให้ {c[0]} : {transfer:,.2f} บาท")
            d[1] -= transfer; c[1] -= transfer
    return jsonify({'settlements': settlements})

@app.route('/post_bill', methods=['POST'])
@login_required
def post_bill():
    data = request.json
    actual_payers = [p['name'] for p in data['payers'] if float(p['amount']) > 0]
    bills_col.insert_one({
        'group_name': data['group_name'], 'bill_name': data['bill_name'],
        'total_amount': data['total_amount'], 'settlements': data['settlements'],
        'payers': actual_payers, 'creator': current_user.username, 'created_at': datetime.now(), 'payments': []
    })
    return jsonify({'status': 'success'})

@app.route('/get_bills/<group_name>', methods=['GET'])
@login_required
def get_bills(group_name):
    bills = list(bills_col.find({'group_name': group_name}).sort('created_at', -1))
    return jsonify({'bills': [{
        'id': str(b['_id']), 'bill_name': b['bill_name'], 'total': b['total_amount'],
        'settlements': b['settlements'], 'payers': b.get('payers', [b['creator']]), 'payments': b.get('payments', [])
    } for b in bills]})

@app.route('/pay_bill', methods=['POST'])
@login_required
def pay_bill():
    data = request.json
    proof_val = data.get('proof', '').strip()
    bills_col.update_one({'_id': ObjectId(data['bill_id'])}, {
        '$push': {'payments': {
            'username': current_user.username, 'proof': proof_val, 'time': datetime.now().strftime("%H:%M")
        }}
    })
    return jsonify({'status': 'success'})

@app.route('/ping')
def ping():
    return "Server is live!", 200

if __name__ == '__main__':
    app.run(debug=True)