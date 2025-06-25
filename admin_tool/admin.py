from datetime import datetime
import logging
import sys
import time
import serial
from serial.tools.list_ports import comports
from PyQt5.QtCore import QObject, pyqtSignal, QMutex
from struct import pack, unpack
from threading import Lock


# --- Константы команд и длин ---
COMMANDS = {
    "read_logo2": b'\x97',
    "write_logo2": b'\x8C',
    "write_logo_roste": b'\x93',
    "read_user_settings": b'\x95',
    "write_user_settings": b'\x8A',
    "read_factory_settings": b'\x9B',
    "get_status": b'\x89',
    "get_plu": b'\x81',
    "delete_plu": b'\x8D',
    "create_plu": b'\x82',
    "get_message": b'\x83',
    "create_message": b'\x84',
    "delete_message": b'\x8E',
    "reset_plu_totals": b'\x92',
    "get_total_sales": b'\x85',
    "reset_total_sales": b'\x86',
    "bind_plu_to_key" : b'\x8B',
    "get_plu_by_key" : b'\x96'

}

LENGTHS = {
    "logo2": 512,
    "logo_roste": 384,
    "user_settings": 9,
    "factory_settings": 13,
    "current_status": 15,
    "plu": 100,
    "message": 400,
    "plu_write": 83,
    "message_write": 402,
    "total_sales": 40,
    "plu_code" : 4,
}

ERROR_RESPONSE = b'\xEE'

# Конфигурация
SERIAL_PORT = '/dev/ttyS1'  # Для Orange Pi
BAUDRATE = 9600

class ScaleAdmin:
    plu_updated = pyqtSignal(dict)  # Сигнал при обновлении данных
    
    def __init__(self, port: str = "COM3", baudrate: str = "9600", ready_callback=None, admin_db = None):
        self.db = admin_db
        self.port = port
        self.ser = None
        self.ready_callback = self._wrap_ready_callback(ready_callback)
        self._ready_state = False
        if port:
            self._connect(port, baudrate)

    def _connect(self, port: str, baudrate: str):
        logging.info(f"Подключение к {port} на {baudrate}")
        try:
            self.ser = serial.Serial(
                port=port,
                baudrate=baudrate,
                bytesize=8,
                parity='N',
                stopbits=1,
                timeout=2,        # 2 секунды на чтение
                write_timeout=3   # 3 секунды на запись
            )
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
            
            
            self.ser.write(b'\x00') # отправляем, чтоб получить b\x80
            self._wait_ready()

            logging.info(f"Порт открыт: {self.ser.is_open}")
        except Exception as e:
            logging.error(f"Ошибка открытия порта: {str(e)}")

    def disconnect(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
            logging.info(f"Порт {self.port} закрыт")

    def is_ready(self) -> bool:
        return getattr(self, "_ready_state", False)

    def _wrap_ready_callback(self, user_callback):
        def wrapper(state):
            self._ready_state = state
            if user_callback:
                user_callback(state)
        return wrapper

    def _wait_ready(self, timeout=2.0) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            byte = self.ser.read(1)
            if not byte:
                continue
            if byte == b'\xEE':
                logging.info("Ошибка выполнения команды (b'\\xEE')")
                # Дочитываем байт готовности
                ready = self.ser.read(1)
                if ready == b'\x80' and self.ready_callback:
                    self.ready_callback(True)
                return True
            if byte == b'\x80':
                logging.info("Получен байт готовности от весов")
                if self.ready_callback:
                    self.ready_callback(True)
                return True
            logging.debug(f"Пропущен байт: {byte.hex()}")
        if self.ready_callback:
            self.ready_callback(False)
        logging.info("Таймаут ожидания байта готовности")
        return False

    def _send_command(self, cmd: bytes, data: bytes = b'', expected_len: int = None) -> bytes:
        if not self.ser.is_open:
            logging.error("Порт не открыт!")
            return b''
        try:
            self.ser.reset_input_buffer()
            logging.info(f"Отправка команды {cmd}, данные: {data.hex()}")
            packet = cmd + data
            self.ser.write(packet)
            self.ser.flush()
            if expected_len and expected_len > 0:
                response = self.ser.read(expected_len)
                if response and response[0:1] == b'\xEE':
                    logging.error("Ошибка выполнения команды (b'\\xEE')")
                    # Дочитываем байт готовности
                    ready = self.ser.read(1)
                    if ready == b'\x80' and self.ready_callback:
                        self.ready_callback(True)
                    return b'\xEE'
                ready = self.ser.read(1)
                if ready == b'\x80' and self.ready_callback:
                    self.ready_callback(True)
                elif self.ready_callback:
                    self.ready_callback(False)
                return response
            else:
                resp = self.ser.read(1)
                if resp == b'\xEE':
                    logging.error("Ошибка выполнения команды (b'\\xEE')")
                    # Дочитываем байт готовности!
                    ready = self.ser.read(1)
                    if ready == b'\x80' and self.ready_callback:
                        self.ready_callback(True)
                    return b'\xEE'
                elif resp == b'\x80':
                    if self.ready_callback:
                        self.ready_callback(True)
                    return b''
                else:
                    if self.ready_callback:
                        self.ready_callback(False)
                    logging.error(f"Неожиданный ответ: {resp.hex()}")
                    return b'\xEE'
        except Exception as e:
            logging.error(f"Ошибка: {str(e)}")
            return b''

    def _check_response(self, response: bytes, expected_len: int, context: str = "") -> bool:
        if not response or len(response) != expected_len:
            logging.error(f"Некорректный ответ {context}: {len(response) if response else 0} байт")
            return False
        return True

    # region PLU Operations
    def get_plu_by_id(self, id: int) -> dict:
        """Получение товара по id"""
        id_bytes = id.to_bytes(4, 'little')
        response = self._send_command(cmd=COMMANDS["get_plu"], data=id_bytes, expected_len=LENGTHS['plu'])  # Отправляем команду

        if response == ERROR_RESPONSE:
            return {}

        if not self._check_response(response, LENGTHS["plu"], "PLU"):
            return {}

        plu = {
            'id': int.from_bytes(response[0:4], 'little'),
            'code': self._bytes_to_str(response[4:10]),
            'name1': self._decode_name(response[10:38]),
            'name2': self._decode_name(response[38:66]),
            'price': int.from_bytes(response[66:70], 'little'),
            'expiry': self._parse_expiry(response[70:73]),
            'tare': int.from_bytes(response[73:75], 'little'),
            'group_code': self._bytes_to_str(response[75:81]),
            'message_number': int.from_bytes(response[81:83], 'little'),
            'last_reset': self.bcd_to_datetime(response[83:89]),
            'total_sum': int.from_bytes(response[89:93], 'little'),
            'total_weight': int.from_bytes(response[93:97], 'little'),
            'sales_count': int.from_bytes(response[97:100], 'little'),
        }

        return plu
    
    def delete_plu_by_id(self, id: int) -> bool:
        """Удаление товара по id"""
        id_bytes = id.to_bytes(4, 'little')
        response = self._send_command(cmd=COMMANDS["delete_plu"], data=id_bytes, expected_len=0)
        return response != ERROR_RESPONSE
    
    def create_plu(self, data: dict) -> bool:
        plu_bytes = self._encode_plu(data)
        response = self._send_command(cmd=COMMANDS["create_plu"], data=plu_bytes, expected_len=0)
        return response != ERROR_RESPONSE

    def reset_plu_totals(self, plu_id: int) -> bool:
        """Обнуляет итоговые данные по PLU с заданным id"""
        data = plu_id.to_bytes(4, 'little')
        response = self._send_command(cmd=COMMANDS['reset_plu_totals'], data=data, expected_len=0)
        return response != ERROR_RESPONSE

    def _encode_plu(self, data: dict) -> bytes:
        """Кодирование данных товара согласно протоколу"""
        expire_type = data.get('expiry_type')
        expiry = data.get('expiry_value')
        if expire_type == 0:
            day, month, year = map(int, expiry.split('.'))
            expiry_bytes = bytes([
                ScaleAdmin._to_bcd(day),
                ScaleAdmin._to_bcd(month),
                ScaleAdmin._to_bcd(year)
            ])
        elif expire_type == 1:
            days = int(expiry)
            expiry_bytes = bytes([
                0x00,
                ScaleAdmin._to_bcd(days // 100),
                ScaleAdmin._to_bcd(days % 100)
            ])
        else:
            raise ValueError(f"expire_type должен быть 0 (дата) или 1 (дни) expiry_type={expire_type}, expiry_value={expiry}")

        parts = [
            # PLU Number (4 bytes)
            data['id'].to_bytes(4, 'little'),
            
            # Item Code (6 bytes)
            self._str_to_bytes(data['code']),
            
            # Название с логотипом
            self._encode_name(data['name1'], data['logo_type'], data.get('cert_code', ''), 0),
            self._encode_name(data['name2'], data['logo_type'], data.get('cert_code', ''), 1),
            
            # Цена (4 bytes)
            int(data['price']).to_bytes(4, 'little'),  # data['price'] в копейках!
            
            # Срок годности (3 bytes)
            expiry_bytes,
            
            # Тара (2 bytes)
            data['tare'].to_bytes(2, 'little'),
            
            # Групповой код (6 bytes)
            self._str_to_bytes(data['group_code']),
            
            # Номер сообщения (2 bytes)
            data['message_number'].to_bytes(2, 'little'),
        ]
        
        # Проверяем типы
        for i, part in enumerate(parts):
            if not isinstance(part, bytes):
                raise TypeError(f"Part {i} is {type(part)}, expected bytes")
        
        # Собираем итоговые данные
        plu_bytes = b''.join(parts)

        if not self._check_response(plu_bytes, LENGTHS["plu_write"], "PLU Write"):
            raise ValueError("Invalid PLU length")
        
        return plu_bytes

    def _encode_name(self, text: str, logo_type: int, cert_code: str, line: int) -> bytes:
        """Кодирует название с логотипом"""
        # Обрезаем строку до 24 символов, если есть логотип
        max_len = 24 if logo_type else 28
        encoded = text.encode('cp1251', errors='replace')[:max_len]
        
        # Дополняем нулями до нужной длины
        padded = encoded.ljust(max_len, b'\x00')
        
        # Добавляем логотип (если требуется)
        if logo_type:
            cert_bytes = self._encode_cert_code(cert_code, line, logo_type)
            return padded + cert_bytes
        return padded

    def _str_to_bytes(self, s: str) -> bytes:
        """Преобразует строку из 6 цифр в 6 байт (каждая цифра — отдельный байт)"""
        s = s.zfill(6)[:6]
        return bytes(int(ch) for ch in s)

    def _bytes_to_str(self, b: bytes) -> str:
        return ''.join(str(byte) for byte in b[:6])

    # def _bytes_to_str(self, b: bytes) -> str:
    #     """Преобразует 6 байт (каждая цифра — отдельный байт) в строку"""
    #     return b[:6].decode('ascii', errors='ignore')
    #     #return ''.join(str(byte) for byte in b[:6])

    def _encode_cert_code(self, cert_code: str, line: int, logo_type: int) -> bytes:
        """Кодирует сертификационный код для логотипа"""
        code = cert_code.ljust(4, '\x00')
        return bytes([
            0,  # Индикатор логотипа
            logo_type,
            ord(code[3 - line]) if len(code) > (3 - line) else 0,
            ord(code[1 + line]) if len(code) > (1 + line) else 0
        ])
    
    def _decode_name(self, name_bytes: bytes) -> str:
        """Декодирует название товара с учетом логотипа"""
        # Определяем длину названия
        if name_bytes[24] == 0:  # Есть логотип
            raw_name = name_bytes[:24]
        else:
            raw_name = name_bytes[:28]
        
        # Удаляем нулевые байты и декодируем
        return raw_name.split(b'\x00')[0].decode('cp1251', errors='ignore')

    def _parse_logo(self, line1: bytes, line2: bytes) -> dict:
        """Извлекает данные логотипа"""
        logo = {}
        if line1[24] == 0 and line2[24] == 0:
            logo = {
                'type': line1[25],
                'cert_code': bytes([line2[26], line1[26], line2[27], line1[27]]).decode('ascii')
            }
        return logo

    def _parse_expiry(self, data: bytes):
        """
        Разбирает срок годности из 3 байт BCD:
        - Если data[0] == 0, то это количество дней (data[1]: сотни, data[2]: десятки и единицы)
        - Иначе это дата: день (data[0]), месяц (data[1]), год (data[2]), все в BCD
        """
        if len(data) != 3:
            return None

        def bcd_to_int(b):
            return ((b >> 4) * 10) + (b & 0x0F)

        if data[0] == 0:
            # Количество дней (BCD)
            hundreds = bcd_to_int(data[1])
            tens_units = bcd_to_int(data[2])
            days = hundreds * 100 + tens_units
            return f"{days}"
        else:
            # Дата (BCD)
            day = bcd_to_int(data[0])
            month = bcd_to_int(data[1])
            year = bcd_to_int(data[2])
            return f"{day:02d}.{month:02d}.{year:02d}"

    @staticmethod
    def _to_bcd(val):
        return ((val // 10) << 4) | (val % 10)

    def bcd_to_datetime(self, bcd_data):
        """Конвертирует 6-байтовый BCD-формат в datetime"""
        if len(bcd_data) != 6:
            return None
            
        second = (bcd_data[0] >> 4) * 10 + (bcd_data[0] & 0x0F)
        minute = (bcd_data[1] >> 4) * 10 + (bcd_data[1] & 0x0F)
        hour = (bcd_data[2] >> 4) * 10 + (bcd_data[2] & 0x0F)
        day = (bcd_data[3] >> 4) * 10 + (bcd_data[3] & 0x0F)
        month = (bcd_data[4] >> 4) * 10 + (bcd_data[4] & 0x0F)
        year = (bcd_data[5] >> 4) * 10 + (bcd_data[5] & 0x0F) + 2000  # Предполагаем 2000+ года
        
        try:
            return datetime(year, month, day, hour, minute, second)
        except ValueError:
            return None
    # endregion

    #region Общие продажи
    def get_total_sales(self) -> dict:
        """Получить общие итоги продаж с весов"""
        response = self._send_command(cmd=COMMANDS['get_total_sales'], expected_len=LENGTHS['total_sales'])
        if response == ERROR_RESPONSE:
            return {}

        if not self._check_response(response, LENGTHS['total_sales'], 'Total sales read'):
            return {}
    
        return {
            'mileage': int.from_bytes(response[0:4], 'little'),
            'label_count': int.from_bytes(response[4:8], 'little'),
            'total_sum': int.from_bytes(response[8:12], 'little'),
            'sales_count': int.from_bytes(response[12:15], 'little'),
            'total_weight': int.from_bytes(response[15:19], 'little'),
            'plu_sum': int.from_bytes(response[19:23], 'little'),
            'plu_sales_count': int.from_bytes(response[23:26], 'little'),
            'plu_weight': int.from_bytes(response[26:30], 'little'),
            'free_plu': int.from_bytes(response[36:38], 'little'),
            'free_msg': int.from_bytes(response[38:40], 'little'),
        }

    def reset_total_sales(self) -> bool:
        """Сбросить общие итоги продаж на весах"""
        response = self._send_command(cmd=COMMANDS['reset_total_sales'], expected_len=0)
        return response != ERROR_RESPONSE
    # endregion

    # region Message Operations
    def get_message_by_id(self, id: int) -> dict:
        """Получение сообщения по id"""
        id_bytes = id.to_bytes(2, 'little')
        response = self._send_command(cmd=COMMANDS["get_message"], data=id_bytes, expected_len=LENGTHS['message'])
        if response == ERROR_RESPONSE:
            return {}

        if not self._check_response(response, LENGTHS['message'], "Message read"):
            return {}

        msg = {
            'id': id,
            'content': response.rstrip(b'\x00').decode('cp1251', errors='ignore')
        }
        return msg
     
    def create_message(self, data: dict) -> bool:
        """Создание нового сообщения"""
        msg_bytes = self._encode_message(data)
        response = self._send_command(cmd=COMMANDS['create_message'], data=msg_bytes, expected_len=0)
        return response != ERROR_RESPONSE
    
    def _encode_message(self, data: dict) -> bytes:
        """Кодирует сообщение в байтовый формат"""
        # Преобразуем текст в байты с кодировкой cp1251
        text_bytes = data['content'].encode('cp1251', errors='replace')
        # Дополняем нулями до 400 байт
        padded_text = text_bytes.ljust(400, b'\x00')
        
        # Собираем итоговые данные
        msg_bytes = data['id'].to_bytes(2, 'little') + padded_text
        
        if not self._check_response(msg_bytes, LENGTHS["message_write"], "Message Write"):
            raise ValueError("Invalid message length")
        
        return msg_bytes
    
    def delete_message_by_id(self, id: int) -> bool:
        """Удаление сообщения по id"""
        id_bytes = id.to_bytes(2, 'little')
        response = self._send_command(cmd=COMMANDS['delete_message'], data=id_bytes, expected_len=0)
        return response != ERROR_RESPONSE
    # endregion

    # --- BCD кодирование и декодирование ---
    @staticmethod
    def int_to_bcd_bytes(value: int, length: int) -> bytes:
        """Преобразует целое число в BCD-байты заданной длины"""
        bcd = []
        for _ in range(length):
            bcd.insert(0, ((value % 10) & 0x0F) | (((value // 10 % 10) << 4) & 0xF0))
            value //= 100
        return bytes(bcd)

    @staticmethod
    def bcd_bytes_to_int(bcd: bytes) -> int:
        """Преобразует BCD-байты в целое число"""
        value = 0
        for b in bcd:
            value = value * 100 + ((b >> 4) & 0x0F) * 10 + (b & 0x0F)
        return value

    #region Настройки пользователя
    def _read_user_settings(self) -> bytes:
        """Чтение настроек пользователя (9 байт, команда 0x95)"""
        response = self._send_command(cmd=COMMANDS['read_user_settings'], expected_len=LENGTHS['user_settings'])
        if response == ERROR_RESPONSE:
            return b''

        if not self._check_response(response, LENGTHS['user_settings'], 'User settings read'):
            return b''
        
        return response

    def _write_user_settings(self, data: bytes) -> bool:
        """Запись настроек пользователя (9 байт, команда 0x8A)"""
        response = self._send_command(cmd=COMMANDS['write_user_settings'], data=data, expected_len=0)
        return response != ERROR_RESPONSE

    def get_user_settings(self) -> dict:
        """Чтение настроек пользователя"""
        data = self._read_user_settings()
        if data:
            return {
                "dept_no": self.bcd_bytes_to_int(data[0:3]),
                "label_format": data[3],
                "barcode_format": data[4],
                "adjst": data[5],
                "print_features": data[6],
                "auto_print_weight": int.from_bytes(data[7:9], "little")
            }
        
        return {}

    def set_user_settings(self, settings: dict) -> bool:
        """Запись настроек пользователя из dict"""
        dept_bcd = self.int_to_bcd_bytes(settings["dept_no"], 3)
        label = settings["label_format"].to_bytes(1, "little")
        barcode = settings["barcode_format"].to_bytes(1, "little")
        adjst = settings["adjst"].to_bytes(1, "little")
        features = settings["print_features"].to_bytes(1, "little")
        auto_weight = settings["auto_print_weight"].to_bytes(2, "little")
        data = dept_bcd + label + barcode + adjst + features + auto_weight
        return self._write_user_settings(data)
    #endregion

    #region Заводские установки
    def _read_factory_settings(self) -> bytes:
        """Чтение заводских установок (13 байт, команда 0x9B)"""
        response = self._send_command(cmd=COMMANDS['read_factory_settings'], expected_len=LENGTHS['factory_settings'])
        if response == ERROR_RESPONSE:
            return b''

        if not self._check_response(response, LENGTHS['factory_settings'], 'Factory settings read'):
            return b''

        return response

    def get_factory_settings(self) -> dict:
        """Чтение заводских установок, возвращает dict"""
        data = self._read_factory_settings()  # читает 13 байт
        if data:
            return {
                "max_weight": int.from_bytes(data[0:2], "little"),
                "dec_point_weight": data[2],
                "dec_point_price": data[3],
                "dec_point_sum": data[4],
                "dual_range": data[5],
                "weight_step_upper": data[6],
                "weight_step_lower": data[7],
                "price_weight": int.from_bytes(data[8:10], "little"),
                "round_sum": data[10],
                "tare_limit": int.from_bytes(data[11:13], "little"),
            }
        
        return {}
    #endregion

    #region Текущее состояние весов
    def _read_current_status(self) -> bytes:
        """Чтение текущего состояния весов (15 байт, команда 0x89)"""
        response = self._send_command(cmd=COMMANDS['get_status'], expected_len=LENGTHS['current_status'])
        if response == ERROR_RESPONSE:
            return {}
        
        if not self._check_response(response, LENGTHS['current_status'], 'Current status read'):
            return b''
        
        return response

    def get_current_status(self) -> dict:
        """Чтение текущего состояния весов, возвращает dict"""
        data = self._read_current_status()  # читает 15 байт
        if not data:
            return {}
        
        status = data[0]
        abs_weight = int.from_bytes(data[1:3], "little")
        if status & 0b10000000:
            weight = -abs_weight
        else:
            weight = abs_weight

        return {
            "status_byte": status,
            "weight": weight,
            "price": int.from_bytes(data[3:7], "little"),
            "sum": int.from_bytes(data[7:11], "little"),
            "plu_number": int.from_bytes(data[11:15], "little"),
            "bits": {
                "overload": bool(status & 0b00000001),
                "tare_mode": bool(status & 0b00000100),
                "zero_weight": bool(status & 0b00001000),
                "dual_range": bool(status & 0b00100000),
                "stable_weight": bool(status & 0b01000000),
                "minus_sign": bool(status & 0b10000000),
            }
        }
    #endregion

    #region Логотипы
    def read_logo2(self) -> bytes:
        """Чтение логотипа LOGO 2"""
        response = self._send_command(cmd=COMMANDS["read_logo2"], expected_len=LENGTHS['logo2'])
        if response == ERROR_RESPONSE:
            return b''
        
        if not self._check_response(response, LENGTHS['logo2'], 'LOGO2 read'):
            return b''

        return response

    def write_logo2(self, data: bytes) -> bool:
        """Запись логотипа LOGO 2"""
        if len(data) != LENGTHS["logo2"]:
            logging.error(f"Длина данных логотипа LOGO 2 должна быть {LENGTHS['logo2']} байт")
            return False
        response = self._send_command(cmd=COMMANDS["write_logo2"], data=data, expected_len=0)
        return response != ERROR_RESPONSE

    def write_logo_roste(self, data: bytes) -> bool:
        """Запись логотипа Ростест"""
        if len(data) != LENGTHS["logo_roste"]:
            logging.error(f"Длина данных логотипа Ростест должна быть {LENGTHS['logo_roste']} байт")
            return False
        response = self._send_command(cmd=COMMANDS["write_logo_roste"], data=data, expected_len=0)
        return response != ERROR_RESPONSE
    #endregion

    #region Клавиши цен
    def get_all_key_binds(self):
        """Вернуть список всех привязок клавиш к PLU"""
        binds = []
        for key_num in range(1, 55):  # 1-54
            plu_id = self.get_plu_by_key(key_num)
            if plu_id:
                binds.append({"key_num": key_num, "plu_id": plu_id})
        return binds

    def bind_plu_to_key(self, key_num: int, plu_id: int) -> bool:
        """Привязать PLU к клавише цены"""
        data = plu_id.to_bytes(4, 'little') + key_num.to_bytes(1, 'little')
        response = self._send_command(cmd=COMMANDS['bind_plu_to_key'], data=data, expected_len=0)
        return response != ERROR_RESPONSE

    def get_plu_by_key(self, key_num: int) -> int:
        """Получить PLU, назначенный на клавишу цены"""
        data = key_num.to_bytes(1, 'little')
        response = self._send_command(COMMANDS["get_plu_by_key"], data, expected_len=LENGTHS['plu_code'])

        if not response or response == ERROR_RESPONSE:
            return None
        
        return int.from_bytes(response, 'little')
    #endregion
