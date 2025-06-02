import logging
import sys
import os
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QListWidget, QStackedWidget,
    QVBoxLayout, QHBoxLayout, QPushButton, QComboBox, QFormLayout, QMessageBox,
    QTableWidget, QHeaderView, QToolBar, QAction, QDialog, QLineEdit, QScrollArea,
    QSpinBox, QDoubleSpinBox, QInputDialog,
    QDialogButtonBox, QStyle, QTableWidgetItem, QMenu, QTabWidget, QCheckBox
)
from PyQt5.QtGui import QIcon, QColor
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QMutex
import serial
import serial.tools.list_ports
from admin import ScaleAdmin
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QPushButton, QLabel, QFileDialog, QHBoxLayout, QComboBox
from PyQt5.QtGui import QPixmap, QImage
import functools
#import numpy as np


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

def require_admin(func):
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        if not self.admin:
            QMessageBox.critical(self, "Ошибка", "Нет подключения к весам")
            return
        return func(self, *args, **kwargs)
    return wrapper

class StatusPoller(QThread):
    status_signal = pyqtSignal(int, int)  # (status_byte, weight)
    
    def __init__(self, admin):
        super().__init__()
        self.admin = admin
        self.mutex = QMutex()
        self._is_running = True

    def run(self):
        while self._is_running:
            if not self.admin.ser.is_open:
                break
            
            self.mutex.lock()
            try:
                response = self.admin.get_status()
                if response and len(response) >= 3:
                    status_byte = response[0]
                    weight = int.from_bytes(response[1:3], 'little', signed=True)
                    self.status_signal.emit(status_byte, weight)
                else:
                    logging.warning("Invalid status response")
            except Exception as e:
                logging.error(f"Ошибка опроса: {str(e)}")
            finally:
                self.mutex.unlock()
                self.msleep(1000)            

    def stop(self):
        self._is_running = False
        self.wait()

class AdminApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.admin = None
        self.status_poller = None
        self.connected = False
        self.setWindowTitle("CAS LP 1.6 Admin Tool")
        self.setGeometry(100, 100, 800, 600)
        self._init_ui()
        sys.excepthook = self.excepthook

    def excepthook(self, exc_type, exc_value, exc_tb):
        logging.error("Необработанное исключение:", exc_info=(exc_type, exc_value, exc_tb))
        self.show_error(f"Критическая ошибка: {str(exc_value)}")
        self.disconnect_from_device()

    def _init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)

        self.sidebar = QListWidget()
        self.sidebar.currentRowChanged.connect(self.change_section)
        self.stacked_widget = QStackedWidget()

        self._init_sections()

        layout = QHBoxLayout(main_widget)
        layout.addWidget(self.sidebar, 1)
        layout.addWidget(self.stacked_widget, 4)

        # Индикатор готовности
        self.ready_indicator = QLabel("Готовность: неизвестно")
        self.ready_indicator.setStyleSheet("background: gray; color: white; padding: 4px;")
        layout.addWidget(self.ready_indicator, 0, Qt.AlignTop)
        logging.info("Интерфейс инициализирован")

    def _init_sections(self):
        self.sidebar.addItems([
            "Конфигурация сети",
            "Товары и сообщения",
            "Настройки весов",
            "Заводские установки",
            "Текущее состояние",
            "Логотип",
            "Тесты"
        ])

        self.user_settings_tab = UserSettingsTab(self.admin, self)
        self.factory_settings_tab = FactorySettingsTab(self.admin, self)
        self.current_status_tab = CurrentStatusTab(self.admin, self)
        self.logo_tab = LogoTab(self.admin, self)

        self.stacked_widget.addWidget(self._create_network_config())
        self.stacked_widget.addWidget(self._create_plu_tab())

        self.stacked_widget.addWidget(self.user_settings_tab)
        self.stacked_widget.addWidget(self.factory_settings_tab)
        self.stacked_widget.addWidget(self.current_status_tab)
        self.stacked_widget.addWidget(self.logo_tab)

        for i in range(1, self.sidebar.count()):
            self.sidebar.item(i).setFlags(Qt.NoItemFlags)

    def change_section(self, index):
        if not self.connected and index != 0:
            self.sidebar.setCurrentRow(0)
            return
        self.stacked_widget.setCurrentIndex(index)

    def set_ready_state(self, ready: bool):
        if ready:
            self.ready_indicator.setText("Готовность: весы готовы")
            self.ready_indicator.setStyleSheet("background: green; color: white; padding: 4px;")
        else:
            self.ready_indicator.setText("Готовность: ожидание весов")
            self.ready_indicator.setStyleSheet("background: red; color: white; padding: 4px;")

    def _create_network_config(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        self.port_combo = QComboBox()
        self.baud_combo = QComboBox()
        self.baud_combo.addItems(['9600', '115200', '57600', '38400'])
        self.btn_refresh_com = QPushButton("Обновить порты")
        self.btn_connect = QPushButton("Подключиться")
        self.status_label = QLabel("Статус: Не подключено")

        form = QFormLayout()
        form.addRow("COM-порт:", self.port_combo)
        form.addRow("Скорость:", self.baud_combo)
        form.addRow(self.btn_refresh_com, self.btn_connect)
        
        self.btn_refresh_com.clicked.connect(self.refresh_ports)
        self.btn_connect.clicked.connect(self.toggle_connection)
        
        layout.addLayout(form)
        layout.addWidget(self.status_label)
        
        self.refresh_ports()
        
        return widget

    def refresh_ports(self):
        self.port_combo.clear()
        ports = [port.device for port in serial.tools.list_ports.comports()]
        self.port_combo.addItems(ports)

    def toggle_connection(self):
        if self.connected:
            self.disconnect_from_device()
        else:
            self.connect_to_device()

    def connect_to_device(self):
        try:
            port = self.port_combo.currentText()
            baudrate = int(self.baud_combo.currentText())
            if not port:
                raise ValueError("Выберите COM-порт")
            self.admin = ScaleAdmin(port=port, baudrate=baudrate, ready_callback=self.set_ready_state)
            if self.admin.ser.is_open:
                self.status_label.setText("Статус: Подключено")
                self.status_label.setStyleSheet("color: green;")
                self.btn_connect.setText("Отключиться")
                self.connected = True
                
                # Активируем остальные вкладки
                for i in range(1, self.sidebar.count()):
                    self.sidebar.item(i).setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)

                # Инициализируем/обновляем admin во всех вкладках
                self.user_settings_tab.admin = self.admin
                self.factory_settings_tab.admin = self.admin
                self.current_status_tab.admin = self.admin
                self.logo_tab.admin = self.admin


        except Exception as e:
            self.show_error(f"Ошибка подключения: {str(e)}")
            self.disconnect_from_device()

    def disconnect_from_device(self):
        if self.status_poller:
            self.status_poller.stop()
            self.status_poller = None
        if self.admin:
            self.admin._disconnect()
            self.admin = None
        for i in range(1, self.sidebar.count()):
            self.sidebar.item(i).setFlags(Qt.NoItemFlags)
        self.connected = False
        self.status_label.setText("Статус: Не подключено")
        self.status_label.setStyleSheet("color: red;")
        self.btn_connect.setText("Подключиться")
        self.plu_table.setRowCount(0)  # Очищаем таблицу при отключении

    def _update_ui(self, status_byte: int, weight: int):
        status_msg = []
        if status_byte & 0b00000001:
            status_msg.append("Перегрузка")
            weight = "N/A"
        elif status_byte & 0b00001000:
            status_msg.append("Нулевой вес")
        elif status_byte & 0b01000000:
            status_msg.append("Стабильно")
        
        self.status_label.setText(
            f"Статус: {' | '.join(status_msg)}\nВес: {weight} г"
            if isinstance(weight, int)
            else f"Статус: {' | '.join(status_msg)}"
        )

    def _create_plu_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        toolbar = QToolBar()

        # Кнопка PLU с выпадающим меню
        plu_menu_btn = QPushButton("PLU")
        plu_menu_btn.setIcon(self._load_icon("plu"))
        plu_menu_btn.setMenu(self._create_plu_menu())
        toolbar.addWidget(plu_menu_btn)

        # Кнопка сообщений с выпадающим меню
        msg_menu_btn = QPushButton("Сообщения")
        msg_menu_btn.setIcon(self._load_icon("message"))
        msg_menu_btn.setMenu(self._create_msg_menu())
        toolbar.addWidget(msg_menu_btn)

        # Кнопка Итоги/Клавиши цен с выпадающим меню
        sales_menu_btn = QPushButton("Итоги/Клавиши цен")
        sales_menu_btn.setIcon(self._load_icon("sales"))
        sales_menu_btn.setMenu(self._create_sales_menu())
        toolbar.addWidget(sales_menu_btn)

        layout.addWidget(toolbar)

        self.plu_table = QTableWidget()
        self.plu_table.setColumnCount(13)
        self.plu_table.setHorizontalHeaderLabels([
            "ID", "Код", "Название 1", "Название 2", "Цена (руб.)", "Дата годн.", "Тара (г)", "Групп. код",
            "Сообщение", "Сброс", "Сумма", "Вес", "Продажи"
        ])
        self.plu_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.plu_table)

        return tab

    def _create_plu_menu(self):
        menu = QMenu()

        add_btn = QAction("Добавить PLU", self)
        add_btn.triggered.connect(self._show_add_plu_dialog)
        menu.addAction(add_btn)

        find_btn = QAction("Найти PLU по id", self)
        find_btn.triggered.connect(self._find_plu_by_id)
        menu.addAction(find_btn)

        del_btn = QAction("Удалить PLU по id", self)
        del_btn.triggered.connect(self._del_plu_by_id)
        menu.addAction(del_btn)

        reset_btn = QAction("Сброс итогов PLU по id", self)
        reset_btn.triggered.connect(self._reset_plu_totals)
        menu.addAction(reset_btn)

        return menu
    
    def _create_msg_menu(self):
        menu = QMenu()

        add_btn = QAction("Добавить сообщение", self)
        add_btn.triggered.connect(self._show_add_message_dialog)
        menu.addAction(add_btn)

        find_btn = QAction("Найти сообщение по id", self)
        find_btn.triggered.connect(self._find_msg_by_id)
        menu.addAction(find_btn)

        del_btn = QAction("Удалить сообщение по id", self)
        del_btn.triggered.connect(self._del_msg_by_id)
        menu.addAction(del_btn)

        return menu

    def _create_sales_menu(self):
        menu = QMenu()

        show_totals = QAction("Показать итоги продаж", self)
        show_totals.setToolTip("Показать текущие итоги продаж по всем товарам")
        show_totals.triggered.connect(self._show_total_sales)
        menu.addAction(show_totals)

        reset_totals = QAction("Сбросить итоги продаж", self)
        reset_totals.setToolTip("Сбросить все итоги продаж (обнулить)")
        reset_totals.triggered.connect(self._reset_total_sales)
        menu.addAction(reset_totals)

        # Клавиши цен
        bind_key = QAction("Привязать PLU к клавише цены", self)
        bind_key.setToolTip("Назначить товар на клавишу цены (1-54)")
        bind_key.triggered.connect(self._bind_plu_to_key)
        menu.addAction(bind_key)

        get_key = QAction("PLU по клавише цены", self)
        get_key.setToolTip("Показать номер товара, назначенный на клавишу цены")
        get_key.triggered.connect(self._get_plu_by_key)
        menu.addAction(get_key)

        return menu

    def _show_add_message_dialog(self):
        dialog = AddMessageDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            # TODO: Реализовать добавление сообщения через admin.py
            self.show_info("Сообщение успешно добавлено")

    def _find_msg_by_id(self):
        id, ok = QInputDialog.getText(self, "Поиск сообщения", "Введите id сообщения:")
        if ok and id:
            try:
                if not id.isdigit() or not (1 <= int(id) < 100):
                    self.show_error("Некорректный id сообщения")
                    return

                msg = self.admin.get_message_by_id(int(id))
                if msg:
                    if isinstance(msg, dict):
                        content = msg.get('content', '')
                    else:
                        content = str(msg)
                    QMessageBox.information(self, "Сообщение найдено", f"ID: {id}\nТекст: {content}")
                else:
                    self.show_error("Сообщение не найдено")
            except Exception as e:
                self.show_error(f"Ошибка поиска: {str(e)}")

    def _del_msg_by_id(self):
        id, ok = QInputDialog.getText(self, "Удаление сообщения", "Введите id сообщения:")
        if ok and id:
            try:
                if not id.isdigit() or not (1 <= int(id) < 100):
                    self.show_error("Некорректный id сообщения")
                    return

                # Подтверждение удаления
                reply = QMessageBox.question(
                    self, 
                    "Подтверждение удаления",
                    f"Вы уверены, что хотите удалить сообщение с кодом {id}?",
                    QMessageBox.Yes | QMessageBox.No
                )

                if reply == QMessageBox.Yes:
                    if self.admin.delete_message_by_id(int(id)):
                        self.show_info("Сообщение успешно удалено")
                    else:
                        self.show_error("Не удалось удалить сообщение")
            except Exception as e:
                self.show_error(f"Ошибка удаления: {str(e)}")

    def _find_plu_by_id(self):
        id, ok = QInputDialog.getText(self, "Поиск PLU", "Введите id товара:")
        if ok and id:
            try:
                if not id.isdigit() or not (1 <= int(id) <= 4000):
                    self.show_error("Некорректный id товара")
                    return
                
                plu = self.admin.get_plu_by_id(int(id))
                if plu and plu != b'\xEE':
                    self._update_plu_table([plu])
                else:
                    self.show_error("Товар не найден")
            except Exception as e:
                self.show_error(f"Ошибка поиска: {str(e)}")
    
    def _del_plu_by_id(self):
        id, ok = QInputDialog.getText(self, "Удаление PLU", "Введите id товара:")
        if ok and id:
            try:
                if not id.isdigit() or not (1 <= int(id) <= 4000):
                    self.show_error("Некорректный id товара")
                    return

                # Подтверждение удаления
                reply = QMessageBox.question(
                    self, 
                    "Подтверждение удаления",
                    f"Вы уверены, что хотите удалить товар с кодом {id}?",
                    QMessageBox.Yes | QMessageBox.No
                )

                if reply == QMessageBox.Yes:
                    if self.admin.delete_plu_by_id(int(id)):
                        self.show_info("Товар успешно удален")
                        # # Подсветить строку с этим id в таблице красным
                        # for row in range(self.plu_table.rowCount()):
                        #     item = self.plu_table.item(row, 0)
                        #     if item and item.text() == str(id):
                        #         for col in range(self.plu_table.columnCount()):
                        #             cell = self.plu_table.item(row, col)
                        #             if cell:
                        #                 cell.setBackground(QColor(180, 0, 0))  # Тёмно-красный
                        #                 cell.setForeground(Qt.white)
                        #         break
                    else:
                        self.show_error("Не удалось удалить товар")
            except Exception as e:
                self.show_error(f"Ошибка удаления: {str(e)}")

    def _reset_plu_totals(self):
        id, ok = QInputDialog.getText(self, "Сброс итогов PLU", "Введите id товара:")
        if ok and id:
            try:
                if not id.isdigit() or not (1 <= int(id) <= 4000):
                    self.show_error("Некорректный id товара")
                    return
                reply = QMessageBox.question(
                    self,
                    "Подтверждение сброса",
                    f"Вы уверены, что хотите сбросить итоги по товару с кодом {id}?",
                    QMessageBox.Yes | QMessageBox.No
                )
                if reply == QMessageBox.Yes:
                    if self.admin.reset_plu_totals(int(id)):
                        self.show_info("Итоги по товару успешно сброшены")
                    else:
                        self.show_error("Не удалось сбросить итоги по товару")
            except Exception as e:
                self.show_error(f"Ошибка сброса: {str(e)}")

    def _load_icon(self, name):
        icon_path = os.path.join("icons", f"{name}.png")
        if os.path.exists(icon_path):
            return QIcon(icon_path)
        else:
            logging.warning(f"Иконка {icon_path} не найдена!")
            return self.style().standardIcon(QStyle.SP_FileIcon)
    
    def _show_add_plu_dialog(self):
        dialog = AddPLUDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            data = dialog.get_data()
            try:
                if self.admin.create_plu(data):
                    self.show_info("Товар успешно добавлен")
                    # # Используем данные, которые только что были добавлены
                    # self._update_plu_table([data])
                    # # Подсветить последнюю строку зелёным
                    # row = self.plu_table.rowCount() - 1
                    # green = QColor(0, 180, 0)
                    # for col in range(self.plu_table.columnCount()):
                    #     item = self.plu_table.item(row, col)
                    #     if item:
                    #         item.setBackground(green)
                else:
                    self.show_error("Не удалось добавить товар")
            except Exception as e:
                self.show_error(f"Ошибка добавления товара: {str(e)}")

    def _show_total_sales(self):
        try:
            totals = self.admin.get_total_sales()
            if not totals:
                self.show_error("Не удалось получить итоги продаж")
                return
            msg = (
                f"Пробег: {totals['mileage']}\n"
                f"Кол-во этикеток: {totals['label_count']}\n"
                f"Сумма продаж: {totals['total_sum']}\n"
                f"Кол-во продаж: {totals['sales_count']}\n"
                f"Общий вес: {totals['total_weight']}\n"
                f"PLU сумма: {totals['plu_sum']}\n"
                f"PLU продажи: {totals['plu_sales_count']}\n"
                f"PLU вес: {totals['plu_weight']}\n"
                f"Свободных PLU: {totals['free_plu']}\n"
                f"Свободных сообщений: {totals['free_msg']}\n"
            )
            QMessageBox.information(self, "Общие итоги продаж", msg)
        except Exception as e:
            self.show_error(f"Ошибка получения итогов: {str(e)}")

    def _reset_total_sales(self):
        reply = QMessageBox.question(
            self, "Сбросить итоги продаж",
            "Вы уверены, что хотите сбросить все итоги продаж?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            try:
                if self.admin.reset_total_sales():
                    self.show_info("Итоги продаж успешно сброшены")
                else:
                    self.show_error("Не удалось сбросить итоги продаж")
            except Exception as e:
                self.show_error(f"Ошибка сброса итогов: {str(e)}")

    def _bind_plu_to_key(self):
        dialog = BindKeyDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            key, plu = dialog.get_values()
            if self.admin.bind_plu_to_key(key, plu):
                self.show_info(f"PLU {plu} успешно назначен на клавишу {key}")
            else:
                self.show_error("Ошибка назначения PLU на клавишу")

    def _get_plu_by_key(self):
        key, ok = QInputDialog.getInt(self, "Клавиша цены", "Введите номер клавиши (1-54):", min=1, max=54)
        if not ok:
            return
        plu = self.admin.get_plu_by_key(key)
        if plu:
            self.show_info(f"На клавише {key} назначен PLU: {plu}")
        else:
            self.show_error("На этой клавише не назначен PLU")

    def _update_plu_table(self, plu_list):
        """
        Добавляет одну строку в таблицу PLU, не затрагивая предыдущие.
        """
        if not plu_list:
            return
        plu = plu_list[0]
        row = self.plu_table.rowCount()
        self.plu_table.insertRow(row)
        # 0: ID
        self.plu_table.setItem(row, 0, QTableWidgetItem(str(plu.get('id', ''))))
        # 1: Код
        self.plu_table.setItem(row, 1, QTableWidgetItem(plu.get('code', '')))
        # 2: Название 1
        self.plu_table.setItem(row, 2, QTableWidgetItem(plu.get('name1', '')))
        # 3: Название 2
        self.plu_table.setItem(row, 3, QTableWidgetItem(plu.get('name2', '')))
        # 4: Цена (коп.)
        self.plu_table.setItem(row, 4, QTableWidgetItem(str(plu.get('price', 0) / 100))) # в рублях
        # 5: Дата годн.
        self.plu_table.setItem(row, 5, QTableWidgetItem(plu.get('expiry', '')))
        # 6: Тара (г)
        self.plu_table.setItem(row, 6, QTableWidgetItem(str(plu.get('tare', ''))))
        # 7: Групп. код
        self.plu_table.setItem(row, 7, QTableWidgetItem(plu.get('group_code', '')))
        # 8: Сообщение
        self.plu_table.setItem(row, 8, QTableWidgetItem(str(plu.get('message_number', ''))))
        # 9: Сброс (дата)
        self.plu_table.setItem(row, 9, QTableWidgetItem(str(plu.get('last_reset', ''))))
        # 10: Сумма
        self.plu_table.setItem(row, 10, QTableWidgetItem(str(plu.get('total_sum', ''))))
        # 11: Вес
        self.plu_table.setItem(row, 11, QTableWidgetItem(str(plu.get('total_weight', ''))))
        # 12: Продажи
        self.plu_table.setItem(row, 12, QTableWidgetItem(str(plu.get('sales_count', ''))))

    def show_error(self, message):
        QMessageBox.critical(self, "Ошибка", message)
        
    def show_info(self, message):
        QMessageBox.information(self, "Информация", message)


class AddMessageDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Новое сообщение")
        self.setFixedSize(500, 300)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        
        form = QFormLayout()
        
        self.msg_id = QSpinBox()
        self.msg_id.setRange(1, 4000)
        form.addRow("ID сообщения:", self.msg_id)
        
        self.msg_text = QLineEdit()
        self.msg_text.setMaxLength(400)
        form.addRow("Текст сообщения:", self.msg_text)
        
        layout.addLayout(form)
        
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self._validate)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _validate(self):
        if not self.msg_id.value():
            QMessageBox.critical(self, "Ошибка", "Введите ID сообщения")
            return
        if not self.msg_text.text().strip():
            QMessageBox.critical(self, "Ошибка", "Введите текст сообщения")
            return
        self.accept()

    def get_data(self):
        return {
            'id': self.msg_id.value(),
            'content': self.msg_text.text()
        }

    def set_data(self, msg_id, text):
        self.msg_id.setValue(msg_id)
        self.msg_text.setText(text)

class AddPLUDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Новый товар")
        self.setFixedSize(600, 500)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        scroll = QScrollArea()
        content = QWidget()
        form = QFormLayout(content)
        
        # Основные поля
        self.plu_id = QSpinBox()
        self.plu_id.setRange(1, 4000)
        form.addRow("PLU ID (1-4000):", self.plu_id)
        
        # Код товара
        self.item_code = QLineEdit()
        self.item_code.setInputMask("999999")  # Только цифры
        self.item_code.setText("000000")  # Значение по умолчанию
        form.addRow("Код товара:", self.item_code)        
        
        self.name1 = QLineEdit()
        self.name1.setMaxLength(28)
        form.addRow("Название строка 1:", self.name1)
        
        self.name2 = QLineEdit()
        self.name2.setMaxLength(28)
        form.addRow("Название строка 2:", self.name2)
        
        # Логотип и сертификаты
        self.logo_combo = QComboBox()
        self.logo_combo.addItems(["Нет", "РОСТЕСТ", "Другой"])
        form.addRow("Логотип:", self.logo_combo)
        
        self.cert_code = QLineEdit()
        self.cert_code.setMaxLength(4)
        form.addRow("Сертификационный код (4 символа):", self.cert_code)
        
        # Цена и тара
        self.price = QDoubleSpinBox()
        self.price.setRange(0, 9999.99)
        form.addRow("Цена (руб.):", self.price)
        
        self.tare = QSpinBox()
        self.tare.setRange(0, 9999)
        form.addRow("Тара (г):", self.tare)
        
        # Срок годности (одно поле, тип зависит от выбора)
        self.expiry_type = QComboBox()
        self.expiry_type.addItems(["Дата", "Дни"])
        form.addRow("Тип срока годности:", self.expiry_type)

        self.expiry_value = QLineEdit()
        self.expiry_value.setPlaceholderText("дд.мм.гг или количество дней")
        form.addRow("Срок годности:", self.expiry_value)

        def update_expiry_field():
            if self.expiry_type.currentIndex() == 0:
                self.expiry_value.setInputMask("00.00.00")
                self.expiry_value.setPlaceholderText("дд.мм.гг")
            else:
                self.expiry_value.setInputMask("999")
                self.expiry_value.setPlaceholderText("Количество дней")
        self.expiry_type.currentIndexChanged.connect(update_expiry_field)
        update_expiry_field()
        
        # Групповой код
        self.group_code = QLineEdit()
        self.group_code.setInputMask("999999")
        self.group_code.setText("000000")
        form.addRow("Групповой код:", self.group_code)

        # Плейсхолдеры
        self.item_code.setPlaceholderText("Введите 6 цифр")
        self.group_code.setPlaceholderText("Введите 6 цифр")

        # Номер сообщения
        self.msg_number = QSpinBox()
        self.msg_number.setRange(0, 1000)
        form.addRow("Номер сообщения:", self.msg_number)
        
        scroll.setWidget(content)
        layout.addWidget(scroll)
        
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self._validate)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _validate(self):
        errors = []
        # Проверка обязательных полей
        if not self.name1.text().strip():
            errors.append("Название (строка 1) обязательно")
        if len(self.item_code.text()) != 6:
            errors.append("Код товара должен содержать 6 цифр")
        if not self.price.value():
            errors.append("Цена должна быть больше 0")
        if not self.tare.value():
            errors.append("Тара должна быть больше или равна 0")
        if self.logo_combo.currentIndex() == 1 and len(self.cert_code.text()) != 4:
            errors.append("Для РОСТЕСТ требуется 4 символа сертификата")
        
        if errors:
            QMessageBox.critical(self, "Ошибки ввода", "\n".join(errors))
        else:
            self.accept()

    def get_data(self):
        return {
            'id': self.plu_id.value(),
            'code': self.item_code.text(),
            'name1': self.name1.text(),
            'name2': self.name2.text(),
            'logo_type': self.logo_combo.currentIndex(),
            'cert_code': self.cert_code.text(),
            'price': int(round(self.price.value() * 100)),  # в копейках
            'tare': self.tare.value(),
            'expiry_type': self.expiry_type.currentIndex(),
            'expiry_value': self.expiry_value.text(),
            'group_code': self.group_code.text(),
            'message_number': self.msg_number.value()
        }

class UserSettingsTab(QWidget):
    def __init__(self, admin = None, parent=None):
        super().__init__(parent)
        self.admin = admin
        layout = QVBoxLayout()
        form = QFormLayout()

        # 1. Номер отдела (BCD, 3 байта, 000-999)
        self.dept_no = QSpinBox()
        self.dept_no.setRange(0, 999)
        form.addRow("Номер отдела (000-999)", self.dept_no)

        # 2. Номер формата этикетки (1 байт, 1-99)
        self.label_format = QSpinBox()
        self.label_format.setRange(1, 99)
        form.addRow("Номер формата этикетки (1-99)", self.label_format)

        # 3. Номер формата штрих-кода (1 байт, 0-8)
        self.barcode_format = QSpinBox()
        self.barcode_format.setRange(0, 8)
        form.addRow("Номер формата штрих-кода (0-8)", self.barcode_format)

        # 4. Сдвиг печати (1 байт, 1-99)
        self.adjst = QSpinBox()
        self.adjst.setRange(1, 99)
        form.addRow("Сдвиг печати (1-99)", self.adjst)

        # 5. Особенности печати (битовая маска, 1 байт)
        self.print_features = []
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
        for feat in features:
            cb = QCheckBox(feat)
            self.print_features.append(cb)
            form.addRow(cb)

        # 6. Величина изменения веса для автопечати (2 байта, граммы)
        self.auto_print_weight = QSpinBox()
        self.auto_print_weight.setRange(0, 65535)
        form.addRow("Величина изменения веса для автопечати (г)", self.auto_print_weight)

        # Кнопки для операций
        self.btn_read = QPushButton("Считать с весов")
        self.btn_write = QPushButton("Записать в весы")
        btns = QHBoxLayout()
        btns.addWidget(self.btn_read)
        btns.addWidget(self.btn_write)
        layout.addLayout(form)
        layout.addLayout(btns)
        self.setLayout(layout)

        # Привязка обработчиков (реализуйте методы ниже)
        self.btn_read.clicked.connect(self.read_settings)
        self.btn_write.clicked.connect(self.write_settings)

    @require_admin
    def read_settings(self, *args, **kwargs):
        settings = self.admin.get_user_settings()
        if settings:
            self.dept_no.setValue(settings["dept_no"])
            self.label_format.setValue(settings["label_format"])
            self.barcode_format.setValue(settings["barcode_format"])
            self.adjst.setValue(settings["adjst"])
            for i, cb in enumerate(self.print_features):
                cb.setChecked(bool(settings["print_features"] & (1 << i)))
            self.auto_print_weight.setValue(settings["auto_print_weight"])

    @require_admin
    def write_settings(self, *args, **kwargs):
        features = 0
        for i, cb in enumerate(self.print_features):
            if cb.isChecked():
                features |= (1 << i)
        settings = {
            "dept_no": self.dept_no.value(),
            "label_format": self.label_format.value(),
            "barcode_format": self.barcode_format.value(),
            "adjst": self.adjst.value(),
            "print_features": features,
            "auto_print_weight": self.auto_print_weight.value()
        }
        self.admin.set_user_settings(settings)

class FactorySettingsTab(QWidget):
    def __init__(self, admin = None, parent=None):
        super().__init__(parent)
        self.admin = admin
        layout = QVBoxLayout()
        form = QFormLayout()

        # Наибольший предел взвешивания (2 байта)
        self.max_weight = QSpinBox()
        self.max_weight.setRange(0, 999999)
        self.max_weight.setReadOnly(True)
        form.addRow("Наибольший предел взвешивания (г)", self.max_weight)

        # Положение десятичной точки
        self.dec_point_weight = QSpinBox()
        self.dec_point_weight.setRange(0, 3)
        self.dec_point_weight.setReadOnly(True)
        form.addRow("Десятичная точка (вес)", self.dec_point_weight)

        self.dec_point_price = QSpinBox()
        self.dec_point_price.setRange(0, 3)
        self.dec_point_price.setReadOnly(True)
        form.addRow("Десятичная точка (цена)", self.dec_point_price)

        self.dec_point_sum = QSpinBox()
        self.dec_point_sum.setRange(0, 3)
        self.dec_point_sum.setReadOnly(True)
        form.addRow("Десятичная точка (стоимость)", self.dec_point_sum)

        # Двухдиапазонный режим
        self.dual_range = QComboBox()
        self.dual_range.addItems(["Выключен", "Включён"])
        self.dual_range.setEnabled(False)
        form.addRow("Двухдиапазонный режим", self.dual_range)

        # Дискретность индикации веса (верхний диапазон)
        self.weight_step_upper = QSpinBox()
        self.weight_step_upper.setRange(1, 1000)
        self.weight_step_upper.setReadOnly(True)
        form.addRow("Дискретность веса (верхний диапазон)", self.weight_step_upper)

        # Дискретность индикации веса (нижний диапазон)
        self.weight_step_lower = QSpinBox()
        self.weight_step_lower.setRange(1, 1000)
        self.weight_step_lower.setReadOnly(True)
        form.addRow("Дискретность веса (нижний диапазон)", self.weight_step_lower)

        # Вес для определения цены (2 байта)
        self.price_weight = QSpinBox()
        self.price_weight.setRange(0, 999999)
        self.price_weight.setReadOnly(True)
        form.addRow("Вес для определения цены (г)", self.price_weight)

        # Величина округления стоимости
        self.round_sum = QSpinBox()
        self.round_sum.setRange(0, 1000)
        self.round_sum.setReadOnly(True)
        form.addRow("Округление стоимости", self.round_sum)

        # Предел выборки тары (2 байта)
        self.tare_limit = QSpinBox()
        self.tare_limit.setRange(0, 999999)
        self.tare_limit.setReadOnly(True)
        form.addRow("Предел выборки тары (г)", self.tare_limit)

        layout.addLayout(form)

        # Только одна кнопка "Считать с весов"
        btns = QHBoxLayout()
        self.btn_read = QPushButton("Считать с весов")
        btns.addWidget(self.btn_read)
        layout.addLayout(btns)
        self.setLayout(layout)

        self.btn_read.clicked.connect(self.read_settings)

    @require_admin
    def read_settings(self, *args, **kwargs):
        settings = self.admin.get_factory_settings()
        if settings:
            self.max_weight.setValue(settings["max_weight"])
            self.dec_point_weight.setValue(settings["dec_point_weight"])
            self.dec_point_price.setValue(settings["dec_point_price"])
            self.dec_point_sum.setValue(settings["dec_point_sum"])
            self.dual_range.setCurrentIndex(settings["dual_range"])
            self.weight_step_upper.setValue(settings["weight_step_upper"])
            self.weight_step_lower.setValue(settings["weight_step_lower"])
            self.price_weight.setValue(settings["price_weight"])
            self.round_sum.setValue(settings["round_sum"])
            self.tare_limit.setValue(settings["tare_limit"])
        else:
            QMessageBox.critical(self, "Ошибка", "Не удалось считать заводские настройки с весов.")

class CurrentStatusTab(QWidget):
    def __init__(self, admin = None, parent=None):
        super().__init__(parent)
        self.admin = admin
        layout = QVBoxLayout()
        form = QFormLayout()

        # 1. Байт состояния
        self.status_byte = QLabel("0x00")
        form.addRow("Байт состояния", self.status_byte)

        # 2. Абсолютное значение веса (2 байта)
        self.weight = QLabel("0 г")
        form.addRow("Вес (г)", self.weight)

        # 3. Цена товара (4 байта)
        self.price = QLabel("0 коп/кг")
        form.addRow("Цена товара (коп/кг)", self.price)

        # 4. Стоимость товара (4 байта)
        self.sum = QLabel("0 копеек")
        form.addRow("Стоимость товара (коп)", self.sum)

        # 5. Номер выбранного товара (4 байта)
        self.plu_number = QLabel("0")
        form.addRow("Номер PLU", self.plu_number)

        # Детализированное состояние по битам
        self.bits_label = QLabel("")
        form.addRow("Детализация состояния", self.bits_label)

        # Кнопка обновления
        self.btn_refresh = QPushButton("Обновить")
        self.btn_refresh.clicked.connect(self.update_status)

        layout.addLayout(form)
        layout.addWidget(self.btn_refresh)
        self.setLayout(layout)

    @require_admin
    def update_status(self, *args, **kwargs):
        status = self.admin.get_current_status()
        if not status:
            self.status_byte.setText("Ошибка чтения")
            return

        self.status_byte.setText(f"0x{status['status_byte']:02X}")
        self.weight.setText(f"{status['weight']} г")
        self.price.setText(f"{status['price']} коп/кг")
        self.sum.setText(f"{status['sum']} копеек")
        self.plu_number.setText(str(status['plu_number']))

        bits = status["bits"]
        bits_str = "; ".join([
            f"Перегрузка: {'Да' if bits['overload'] else 'Нет'}",
            f"Выборка тары: {'Да' if bits['tare_mode'] else 'Нет'}",
            f"Нулевой вес: {'Да' if bits['zero_weight'] else 'Нет'}",
            f"Двухдиапазонный режим: {'Да' if bits['dual_range'] else 'Нет'}",
            f"Стабильный вес: {'Да' if bits['stable_weight'] else 'Нет'}",
            f"Минус (знак веса): {'Да' if bits['minus_sign'] else 'Нет'}",
        ])
        self.bits_label.setText(bits_str)

class LogoTab(QWidget):
    def __init__(self, admin = None, parent=None):
        super().__init__(parent)
        self.admin = admin

        layout = QVBoxLayout()
        self.logo_type = QComboBox()
        self.logo_type.addItems(["LOGO 2 (64x64)", "Ростест (64x48)"])
        layout.addWidget(self.logo_type)

        self.img_label = QLabel("Нет изображения")
        self.img_label.setFixedSize(256, 256)
        layout.addWidget(self.img_label)

        btns = QHBoxLayout()
        self.btn_read = QPushButton("Прочитать с весов")
        self.btn_load = QPushButton("Загрузить из файла")
        self.btn_save = QPushButton("Сохранить в файл")
        self.btn_write = QPushButton("Записать в весы")
        btns.addWidget(self.btn_read)
        btns.addWidget(self.btn_load)
        btns.addWidget(self.btn_save)
        btns.addWidget(self.btn_write)
        layout.addLayout(btns)

        self.setLayout(layout)

        self.btn_read.clicked.connect(self.read_logo)
        self.btn_load.clicked.connect(self.load_logo)
        self.btn_save.clicked.connect(self.save_logo)
        self.btn_write.clicked.connect(self.write_logo)

        self.logo_bytes = None

    @require_admin
    def read_logo(self, *args, **kwargs):
        if self.logo_type.currentIndex() == 0:
            data = self.admin.read_logo2()
            w, h = 64, 64
        else:
            # Для Ростест только запись, чтения нет по протоколу
            QMessageBox.information(self, "Информация", "Чтение логотипа Ростест не поддерживается.")
            return
        if data:
            self.logo_bytes = data
            self.show_logo(data, w, h)

    @require_admin
    def write_logo(self, *args, **kwargs):
        if not self.logo_bytes:
            return
        if self.logo_type.currentIndex() == 0:
            ok = self.admin.write_logo2(self.logo_bytes)
        else:
            ok = self.admin.write_logo_roste(self.logo_bytes)
        if ok:
            QMessageBox.information(self, "Успех", "Логотип успешно записан.")
        else:
            QMessageBox.critical(self, "Ошибка", "Ошибка записи логотипа.")

    def load_logo(self):
        path, _ = QFileDialog.getOpenFileName(self, "Открыть картинку", "", "Images (*.png *.bmp)")
        if not path:
            return
        img = QImage(path)
        w, h = (64, 64) if self.logo_type.currentIndex() == 0 else (64, 48)
        img = img.convertToFormat(QImage.Format_Mono)
        img = img.scaled(w, h)
        # Преобразуем QImage в массив байтов
        bits = []
        for y in range(h):
            for x in range(0, w, 8):
                byte = 0
                for b in range(8):
                    if x + b < w:
                        color = img.pixelColor(x + b, y)
                        if color.black() > 0:  # чёрный пиксель
                            byte |= (1 << (7 - b))
                bits.append(byte)
        self.logo_bytes = bytes(bits)
        self.show_logo(self.logo_bytes, w, h)

    def save_logo(self):
        if not self.logo_bytes:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить логотип", "", "BIN (*.bin)")
        if path:
            with open(path, "wb") as f:
                f.write(self.logo_bytes)

    def show_logo(self, data, w, h):
        # Преобразуем байты обратно в QImage для отображения
        img = QImage(w, h, QImage.Format_Mono)
        for y in range(h):
            for x in range(w):
                byte_index = (y * w + x) // 8
                bit_index = 7 - (x % 8)
                if data[byte_index] & (1 << bit_index):
                    img.setPixel(x, y, 1)
                else:
                    img.setPixel(x, y, 0)
        pix = QPixmap.fromImage(img).scaled(256, 256)
        self.img_label.setPixmap(pix)

class BindKeyDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Привязка PLU к клавише цены")
        layout = QFormLayout(self)
        self.key_spin = QSpinBox()
        self.key_spin.setRange(1, 54)
        self.plu_spin = QSpinBox()
        self.plu_spin.setRange(1, 4000)
        layout.addRow("Номер клавиши (1-54):", self.key_spin)
        layout.addRow("Номер PLU (1-4000):", self.plu_spin)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def get_values(self):
        return self.key_spin.value(), self.plu_spin.value()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = AdminApp()
    window.show()
    sys.exit(app.exec_())