# app.py
import sys
from flask import Flask, jsonify, render_template, request, redirect, url_for, flash
from admin import ScaleAdmin
import serial
import logging
import os

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

app = Flask(__name__)
app.secret_key = 'your_secret_key'

# Конфигурация
SERIAL_PORT = '/dev/ttyS1'  # Для Orange Pi
BAUDRATE = 9600

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

def set_scales_ready(state):
    global scales_ready
    scales_ready = state

@app.route("/", methods=["GET"])
def index():
    ports = [port.device for port in serial.tools.list_ports.comports()]
    scales_ready = False
    if connection["admin"]:
        scales_ready = connection["admin"].is_ready()
    else:
        connection["status_message"] = "Нет подключения к com-порту"

    return render_template(
        "index.html",
        ports=ports,
        current_port=connection["current_port"],
        current_baudrate=connection["current_baudrate"],
        connected=connection["connected"],
        connection_status={"connected": connection["connected"], "message": connection["status_message"]} if connection["status_message"] else None,
        scales_ready=scales_ready
    )

@app.route("/connect", methods=["POST"])
def connect():
    port = request.form.get("port")
    baudrate = int(request.form.get("baudrate"))
    if connection["connected"]:
        # Отключение
        if connection["admin"]:
            try:
                connection["admin"].ser.close()
            except Exception:
                pass
        connection["admin"] = None
        connection["connected"] = False
        connection["current_port"] = None
        connection["current_baudrate"] = None
        connection["status_message"] = "Отключено"
        flash("Отключено", "success")
    else:
        # Подключение
        try:
            admin = ScaleAdmin(port=port, baudrate=baudrate, ready_callback=set_scales_ready)
            if admin.ser.is_open:
                connection["admin"] = admin
                connection["connected"] = True
                connection["current_port"] = port
                connection["current_baudrate"] = baudrate
                connection["status_message"] = f"Успешно подключено к {port} ({baudrate})"
            else:
                connection["status_message"] = "Не удалось подключиться к весам"
        except Exception as e:
            connection["status_message"] = f"Ошибка подключения: {str(e)}"
            flash(f"Ошибка подключения: {str(e)}", "danger")
    return redirect(url_for("index"))

@app.route("/refresh_ports")
def refresh_ports():
    ports = [port.device for port in serial.tools.list_ports.comports()]
    return jsonify({"ports": ports})

def get_admin_connection():
    if not connection["connected"]:
        flash("Нет подключения к com-порту", "danger")
        return redirect(url_for("index"))
    return connection["admin"]
     
@app.route("/plu", methods=["GET"])
def plu():
    admin = get_admin_connection()
    active_tab = request.args.get("active_tab", "plu")
    return render_template("plu.html", plu_list=plu_list, messages=messages, scales_ready=scales_ready, active_tab=active_tab)

#region PLU management
@app.route("/add_plu", methods=["POST"])
def add_plu():
    admin = get_admin_connection()
    data = request.form.to_dict()

    plu_data = {
        'id': int(data['id']),
        'code': data['code'],
        'name1': data['name1'],
        'name2': data.get('name2', ''),
        'logo_type': int(data.get('logo_type', 0)),
        'cert_code': data.get('cert_code', ''),
        'price': int(float(data['price']) * 100),  # в копейках
        'tare': int(data['tare']),
        'expiry_type': int(data['expiry_type']),
        'expiry_value': data.get('expiry_value', ''),
        'group_code': data['group_code'],
        'message_number': int(data['message_number'])
    }

    ok = admin.create_plu(plu_data)
    if ok:
        flash("Товар успешно добавлен", "success")
    else:
        flash("Не удалось добавить товар", "danger")
        
    return '', 204

@app.route("/find_plu", methods=["GET"])
def find_plu():
    admin = get_admin_connection()
    plu_id = int(request.args.get("id"))
    plu = admin.get_plu_by_id(plu_id)
    if plu:
        # Не добавлять дубликаты
        if not any(p['id'] == plu['id'] for p in plu_list):
            plu_list.append(plu)
        return render_template("plu.html", plu_list=plu_list, messages=messages, scales_ready=scales_ready)
    else:
        flash(f"Товар с ID {plu_id} не найден", "warning")
        return render_template("plu.html", plu_list=plu_list, messages=messages, active_tab='plu', scales_ready=scales_ready)

@app.route("/delete_plu", methods=["POST"])
def delete_plu():
    admin = get_admin_connection()
    plu_id = int(request.form.get("id"))
    ok = admin.delete_plu_by_id(plu_id)
    if ok:
        flash("Товар удалён", "success")
    else:
        flash("Не удалось удалить товар", "danger")

    return '', 204

@app.route("/reset_plu_totals", methods=["POST"])
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
def get_total_sales_table():
    admin = get_admin_connection()
    totals = admin.get_total_sales() if hasattr(admin, "get_total_sales") else {}
    return render_template("partials/total_sales_table.html", plu_list=plu_list,
                            messages=messages, totals=totals, active_tab='sales', scales_ready=scales_ready)

@app.route("/reset_total_sales", methods=["POST"])
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
def read_user_settings():
    admin = get_admin_connection()
    settings = admin.get_user_settings()
    return jsonify(settings)

@app.route('/user_settings')
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
def factory_settings():
    admin = get_admin_connection()
    
    global FACTORY_SETTINGS
    if not FACTORY_SETTINGS:
        FACTORY_SETTINGS = admin.get_factory_settings() if admin else {}

    return render_template('factory_settings.html', settings=FACTORY_SETTINGS, scales_ready=scales_ready)

#endregion

@app.route('/get_current_status')
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
def current_status():
    admin = get_admin_connection()
    
    return render_template('current_status.html', status={}, scales_ready=scales_ready)

@app.route('/logo')
def logo():
    admin = get_admin_connection()
    
    return render_template('logo.html', scales_ready=scales_ready)

@app.route("/remove_plu_from_table", methods=["POST"])
def remove_plu_from_table():
    global plu_list
    plu_id = int(request.form.get("id"))
    plu_list = [plu for plu in plu_list if plu['id'] != plu_id]
    return '', 204

@app.route("/remove_message_from_table", methods=["POST"])
def remove_message_from_table():
    global messages
    msg_id = int(request.form.get("id"))
    messages = [msg for msg in messages if msg['id'] != msg_id]
    return '', 204

@app.route("/clear_plu_table", methods=["POST"])
def clear_plu_table():
    global plu_list
    plu_list = []
    return '', 204

@app.route("/clear_messages_table", methods=["POST"])
def clear_messages_table():
    global messages
    messages = []
    return '', 204

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)