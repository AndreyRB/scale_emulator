# app.py
from datetime import datetime
import sys
from flask import Flask, jsonify, render_template, request, redirect, url_for, flash
from flask_login import LoginManager, login_user, login_required, logout_user, UserMixin
from functools import wraps
import serial
import logging
import os
from admin_db import AdminDatabase
from admin import ScaleAdmin

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

app = Flask(__name__)
app.secret_key = 'your_secret_key'

db = AdminDatabase()
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'  # если не авторизован — редирект на /login

class User(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

    @staticmethod
    def get(user_id):
        user = db.get_user_by_id(user_id)
        if user:
            return User(user['id'], user['username'])
        return None

@login_manager.user_loader
def load_user(user_id):
    return User.get(user_id)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = _check_user_in_db(username, password)
        if user:
            login_user(user)
            return redirect(url_for('index'))
        else:
            flash('Неверный логин или пароль', 'danger')
    return render_template('login.html')

def _check_user_in_db(username, password):
    user = db.check_user(username, password)
    if user:
        return User(user['id'], user['username'])
    return None

@app.route('/logout', methods=['POST'])
@login_required
def logout():
    # Разрываем соединение с весами
    if connection["admin"]:
        try:
            connection["admin"].disconnect()
        except Exception:
            pass
        connection["admin"] = None
        connection["connected"] = False
        connection["current_port"] = None
        connection["current_baudrate"] = None
        connection["status_message"] = "Отключено"
    logout_user()
    return redirect(url_for('login'))

scales_ready = False
plu_list = []      
messages = []     
FACTORY_SETTINGS = {}

# Глобальное состояние подключения
connection = {
    "admin": None,
    "connected": False,
    "current_port": None,
    "current_baudrate": None,
    "status_message": ""
}

def require_scales_ready(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not scales_ready:
            flash("Весы не готовы к работе!", "danger")
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def set_scales_ready(state):
    global scales_ready
    scales_ready = state


@app.route("/", methods=["GET"])
@login_required
def index():
    admin = get_admin_connection()
    return render_template(
        "index.html",
        current_port=connection["current_port"],
        current_baudrate=connection["current_baudrate"],
        connected=connection["connected"],
        connection_status={"connected": connection["connected"], "message": connection["status_message"]},
        scales_ready=scales_ready
    )

def get_admin_connection():
    if not connection["connected"]:
        try:
            admin = ScaleAdmin(ready_callback=set_scales_ready, admin_db=db)
            if admin.ser.is_open:
                connection["admin"] = admin
                connection["connected"] = True
                connection["current_port"] = admin.ser.port
                connection["current_baudrate"] = admin.ser.baudrate
                connection["status_message"] = f"Успешно подключено к {admin.ser.port} ({admin.ser.baudrate})"
            else:
                connection["status_message"] = "Не удалось подключиться к весам"
                flash(connection["status_message"], "danger")
                return redirect(url_for("index"))
        except Exception as e:
            connection["status_message"] = f"Ошибка подключения: {str(e)}"
            flash(connection["status_message"], "danger")
            return redirect(url_for("index"))
    return connection["admin"]

import signal

def handle_exit(signum, frame):
    if connection["admin"]:
        try:
            connection["admin"].disconnect()
            logging.info("Порт закрыт по сигналу завершения")
        except Exception:
            pass
    sys.exit(0)

signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)

@app.route("/plu", methods=["GET"])
@login_required
def plu():
    plu_list = db.get_all_plu()
    return render_template("plu.html", plu_list=plu_list, messages=messages, scales_ready=scales_ready, active_tab="plu")

#region PLU management
#region sync management
from threading import Thread

imported_plu_list = []

sync_status = {
    "in_progress": False,
    "total": 0,
    "current": 0,
    "errors": [],
    "direction": "",  # "to_scales" или "from_scales"
    "done": False
}

@app.route("/sync_status")
@login_required
@require_scales_ready
def get_sync_status():
    return jsonify(sync_status)

@app.route("/sync_history")
@login_required
@require_scales_ready
def sync_history():
    history = db.get_sync_history()
    plu_list = db.get_all_plu()
    return render_template("sync_history.html", history=history, plu_list=plu_list, scales_ready=scales_ready)

@app.route("/start_sync_plu_to_scales", methods=["POST"])
@login_required
@require_scales_ready
def start_sync_plu_to_scales():
    if not sync_status["in_progress"]:
        Thread(target=sync_plu_to_scales_async).start()
    return '', 204

@app.route("/start_sync_selected_plu_to_scales", methods=["POST"])
@login_required
@require_scales_ready
def start_sync_selected_plu_to_scales():
    ids = request.json.get("ids", [])
    if not sync_status["in_progress"]:
        Thread(target=sync_selected_plu_to_scales_async, args=(ids,)).start()
    return '', 204

@app.route("/start_sync_changed_plu_to_scales", methods=["POST"])
@login_required
@require_scales_ready
def start_sync_changed_plu_to_scales():
    if not sync_status["in_progress"]:
        Thread(target=sync_changed_plu_to_scales_async).start()
    return '', 204

def sync_selected_plu_to_scales_async(ids):
    sync_status.update({"in_progress": True, "direction": "to_scales", "done": False, "errors": []})
    selected_plu = [plu for plu in db.get_all_plu() if str(plu['id']) in ids]
    sync_status["total"] = len(selected_plu)
    sync_status["current"] = 0
    admin = get_admin_connection()
    for plu in selected_plu:
        ok = admin.create_plu(plu)
        if not ok:
            sync_status["errors"].append(plu['id'])
        sync_status["current"] += 1
    sync_status["in_progress"] = False
    sync_status["done"] = True
    db.add_sync_history("to_scales", sync_status["total"], sync_status["errors"])

def sync_changed_plu_to_scales_async():
    # Получаем время последней синхронизации из истории
    last_sync = db.get_last_sync_time("to_scales")
    changed_plu = db.get_changed_plu(last_sync) if last_sync else db.get_all_plu()
    sync_status.update({"in_progress": True, "direction": "to_scales", "done": False, "errors": []})
    sync_status["total"] = len(changed_plu)
    sync_status["current"] = 0
    admin = get_admin_connection()
    for plu in changed_plu:
        ok = admin.create_plu(plu)
        if not ok:
            sync_status["errors"].append(plu['id'])
        sync_status["current"] += 1
    sync_status["in_progress"] = False
    sync_status["done"] = True
    db.add_sync_history("to_scales", sync_status["total"], sync_status["errors"])

def sync_plu_to_scales_async():
    sync_status.update({"in_progress": True, "direction": "to_scales", "done": False, "errors": []})
    all_plu = db.get_all_plu()
    sync_status["total"] = len(all_plu)
    sync_status["current"] = 0
    admin = get_admin_connection()
    for plu in all_plu:
        ok = admin.create_plu(plu)
        if not ok:
            sync_status["errors"].append(plu['id'])
        sync_status["current"] += 1
    sync_status["in_progress"] = False
    sync_status["done"] = True
    db.add_sync_history("to_scales", sync_status["total"], sync_status["errors"])

@app.route("/import_selected_plu_from_scales", methods=["POST"])
@login_required
@require_scales_ready
def import_selected_plu_from_scales():
    ids = request.json.get("ids", [])
    admin = get_admin_connection()
    found = []
    for plu_id in ids:
        plu = admin.get_plu_by_id(int(plu_id))
        if plu and plu.get('id'):
            found.append(normalize_plu_for_web(plu))
    # глобальная переменная для тестирования (потом изменить)
    global imported_plu_list
    imported_plu_list = found
    return jsonify({"plu_list": found})

@app.route("/save_imported_plu", methods=["POST"])
@login_required
def save_imported_plu():
    ids = request.json.get("ids", [])
    saved = []
    for plu in imported_plu_list:
        if str(plu['id']) in ids:
            db.upsert_plu(plu)
            saved.append(plu['id'])
    return jsonify({"saved": saved})


def normalize_plu_for_web(plu):
    # expiry -> expiry_value и expiry_type
    if 'expiry' in plu:
        plu['expiry_value'] = plu['expiry']
        plu['expiry_type'] = 0 if '.' in plu['expiry'] else 1
    # last_reset: datetime -> строка
    if isinstance(plu.get('last_reset'), datetime):
        plu['last_reset'] = plu['last_reset'].strftime("%d.%m.%y %H:%M:%S")
    elif plu.get('last_reset') is None:
        plu['last_reset'] = ""
    # logo_type по умолчанию 0, если нет
    if 'logo_type' not in plu:
        plu['logo_type'] = 0
    # cert_code по умолчанию пустая строка
    if 'cert_code' not in plu:
        plu['cert_code'] = ''
    return plu

#endregion
@app.route("/add_plu", methods=["POST"])
@login_required
def add_plu():
    data = request.form.to_dict()
    plu_data = {
        'id': int(data['id']),
        'code': data['code'],
        'name1': data['name1'],
        'name2': data.get('name2', ''),
        'logo_type': int(data.get('logo_type', 0)),
        'cert_code': data.get('cert_code', ''),
        'price': int(float(data['price']) * 100),
        'tare': int(data['tare']),
        'expiry_type': int(data['expiry_type']),
        'expiry_value': data.get('expiry_value', ''),
        'group_code': data['group_code'],
        'message_number': int(data['message_number'])
    }
    ok = db.upsert_plu(plu_data)
    if ok:
        flash("Товар успешно добавлен в серверную базу", "success")
    else:
        flash("Не удалось добавить товар", "danger")
    return '', 204

@app.route("/find_plu", methods=["GET"])
@login_required
def find_plu():
    plu_id = int(request.args.get("id"))
    plu = db.get_plu(plu_id)
    if plu:
        if not any(p['id'] == plu['id'] for p in plu_list):
            plu_list.append(plu)
        return render_template("plu.html", plu_list=plu_list, messages=messages, scales_ready=scales_ready)
    else:
        flash(f"Товар с ID {plu_id} не найден в серверной базе", "warning")
        return render_template("plu.html", plu_list=plu_list, messages=messages, active_tab='plu', scales_ready=scales_ready)

@app.route("/delete_plu", methods=["POST"])
@login_required
def delete_plu():
    plu_id = int(request.form.get("id"))
    ok = db.clear_plu(plu_id)
    if ok:
        flash("Товар удалён из серверной базы", "success")
    else:
        flash("Не удалось удалить товар", "danger")
    return '', 204

@app.route("/reset_plu_totals", methods=["POST"])
@login_required
def reset_plu_totals():
    admin = get_admin_connection()
    plu_id = int(request.form.get("id"))
    ok = admin.reset_plu_totals(plu_id)
    if ok:
        flash("Итоги по товару успешно сброшены", "success")
    else:
        flash("Не удалось сбросить итоги по товару", "danger")
    
    return '', 204
#endregion

#region Message management
@app.route("/add_message", methods=["POST"])
@login_required
def add_message():
    admin = get_admin_connection()
    data = request.form.to_dict()
    data['id'] = int(data['id'])
    ok = admin.create_message(data)
    if ok:
        flash("Сообщение успешно добавлено", "success")
    else:
        flash("Не удалось добавить сообщение", "danger")
    
    return '', 204

@app.route("/find_message", methods=["GET"])
@login_required
def find_message():
    admin = get_admin_connection()
    msg_id = int(request.args.get("id"))
    msg = admin.get_message_by_id(msg_id)
    
    if msg:
        if not any(m['id'] == msg['id'] for m in messages):
             messages.append({'id': msg_id, 'content': msg['content']})
        return render_template("plu.html", plu_list=plu_list, messages=messages, active_tab='messages', scales_ready=scales_ready)
    else:
        flash(f"Сообщение с ID {msg_id} не найдено", "warning")
        return render_template("plu.html", plu_list=plu_list, messages=messages, active_tab='messages', scales_ready=scales_ready)


@app.route("/delete_message", methods=["POST"])
@login_required
def delete_message():
    admin = get_admin_connection()
    msg_id = int(request.form.get("id"))
    ok = admin.delete_message_by_id(msg_id)
    if ok:
        flash("Сообщение удалено", "success")
    else:
        flash("Не удалось удалить сообщение", "danger")

    return '', 204
#endregion

#region Total sales management
@app.route("/get_total_sales_table")
@login_required
def get_total_sales_table():
    admin = get_admin_connection()
    totals = admin.get_total_sales() if hasattr(admin, "get_total_sales") else {}
    return render_template("partials/total_sales_table.html", plu_list=plu_list,
                            messages=messages, totals=totals, active_tab='sales', scales_ready=scales_ready)

@app.route("/reset_total_sales", methods=["POST"])
@login_required
def reset_total_sales():
    admin = get_admin_connection()
    ok = admin.reset_total_sales() if hasattr(admin, "reset_total_sales") else False
    if ok:
        flash("Итоги продаж успешно сброшены", "success")
    else:
        flash("Не удалось сбросить итоги продаж", "danger")

    return '', 204
#endregion

#region Bind managment
@app.route("/bind_plu_to_key", methods=["POST"])
@login_required
def bind_plu_to_key():
    admin = get_admin_connection()
    key_num = int(request.form.get("key_num"))
    plu_id = int(request.form.get("plu_id"))
    ok = admin.bind_plu_to_key(key_num, plu_id)
    if ok:
        return jsonify({"success": True, "message": f"PLU {plu_id} успешно назначен на клавишу {key_num}"})
    else:
        return jsonify({"success": False, "message": "Ошибка назначения PLU на клавишу"}), 400
    
@app.route("/get_plu_by_key", methods=["GET"])
@login_required
def get_plu_by_key():
    admin = get_admin_connection()
    key_num = int(request.args.get("key_num"))
    plu = admin.get_plu_by_key(key_num)
    if plu is None or plu == 0:
        return jsonify({"success": True, "plu": "Не привязано"})
    else:
        return jsonify({"success": True, "plu": plu})
#endregion

#region Settings management
@app.route('/save_user_settings', methods=['POST'])
@login_required
def save_user_settings():
    admin = get_admin_connection()
    settings = request.form.to_dict()
    for key in ['dept_no', 'label_format', 'barcode_format', 'adjst', 'auto_print_weight']:
        if key in settings:
            settings[key] = int(settings[key])
    print_features = 0

    for i in range(8):
        if settings.get(f'print_feature{i}'):
            print_features |= (1 << i)

    settings['print_features'] = print_features

    ok = admin.set_user_settings(settings)
    if ok:
        flash("Настройки успешно сохранены", "success")
    else:
        flash("Не удалось сохранить настройки", "danger")

    return redirect(url_for("user_settings"))

@app.route('/read_user_settings')
@login_required
def read_user_settings():
    admin = get_admin_connection()
    settings = admin.get_user_settings()
    return jsonify(settings)

@app.route('/user_settings')
@login_required
def user_settings():
    admin = get_admin_connection()
    settings = admin.get_user_settings() if admin else {}
    features = [
        "Изменение цены разрешено",
        "Изменение цены с сохранением",
        "Печать номера PLU",
        "Печать группового кода",
        "Печать даты упаковки",
        "Печать срока годности",
        "Печать номера этикетки",
        "Печать времени упаковки"
    ]
    # Получаем битовую маску
    flags = []
    pf = settings.get('print_features', 0)
    for i in range(8):
        flags.append(bool(pf & (1 << i)))
    return render_template(
        'user_settings.html',
        settings=settings,
        features=features,
        print_features_flags=flags,
        scales_ready=scales_ready
    )

@app.route('/factory_settings')
@login_required
def factory_settings():
    admin = get_admin_connection()
    
    global FACTORY_SETTINGS
    if not FACTORY_SETTINGS:
        FACTORY_SETTINGS = admin.get_factory_settings() if admin else {}

    return render_template('factory_settings.html', settings=FACTORY_SETTINGS, scales_ready=scales_ready)

#endregion

#region Status management
@app.route('/get_current_status')
@login_required
def get_current_status():
    admin = get_admin_connection()
    status = admin.get_current_status()
    
    result = {
        "status_byte_hex": hex(status.get("status_byte", 0)),
        "weight": status.get("weight", ""),
        "price": status.get("price", ""),
        "sum": status.get("sum", ""),
        "plu_number": status.get("plu_number", ""),
        "bits_str": "\n".join(f"{k}: {v}" for k, v in status.get("bits", {}).items()),
        "ready": scales_ready
    }

    return jsonify(result)

@app.route('/current_status')
@login_required
def current_status():
    admin = get_admin_connection()
    
    return render_template('current_status.html', status={}, scales_ready=scales_ready)
#endregion

#region Logo management
@app.route('/logo')
@login_required
def logo():
    admin = get_admin_connection()
    
    return render_template('logo.html', scales_ready=scales_ready)
#endregion

#region UX plu and messages management
@app.route("/remove_plu_from_table", methods=["POST"])
@login_required
def remove_plu_from_table():
    global plu_list
    plu_id = int(request.form.get("id"))
    plu_list = [plu for plu in plu_list if plu['id'] != plu_id]
    return '', 204

@app.route("/remove_message_from_table", methods=["POST"])
@login_required
def remove_message_from_table():
    global messages
    msg_id = int(request.form.get("id"))
    messages = [msg for msg in messages if msg['id'] != msg_id]
    return '', 204

@app.route("/clear_plu_table", methods=["POST"])
@login_required
def clear_plu_table():
    global plu_list
    plu_list = []
    return '', 204

@app.route("/clear_messages_table", methods=["POST"])
@login_required
def clear_messages_table():
    global messages
    messages = []
    return '', 204
#endregion

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)