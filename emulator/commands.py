# commands.py
import random
from struct import pack, unpack
from datetime import datetime
import logging
import sqlite3
from .database import ScaleDatabase

from dataclasses import dataclass

@dataclass
class PLU:
    # Read/Write Zone (83 bytes)
    plu_number: int               # 4 bytes (0000H-0003H), range 0-4000
    item_code: str                 # 6 BCD bytes (0004H-0009H), e.g. "123456"
    name_line1: str                # 28 ASCII bytes (000AH-0025H)
    name_line2: str                # 28 ASCII bytes (0026H-0041H)
    price: int                     # 4 bytes (0042H-0045H), max 999999
    expiry_type: int               # 0 = days, 1 = fixed date
    expiry_days: int               # 0-999 (if type=0)
    expiry_date: tuple             # (day, month, year) (if type=1)
    tare_weight: int               # 2 bytes (0049H-004AH)
    group_code: str                # 6 BCD bytes (004BH-0050H)
    message_number: int            # 2 bytes (0051H-0052H), range 0-1000

    # Read-Only Zone (17 bytes)
    last_reset_datetime: tuple     # (sec, min, hour, day, month, year)
    total_sales_amount: int        # 4 bytes (0059H-005CH)
    total_sales_weight: int        # 4 bytes (005DH-0060H)
    total_sales_count: int         # 3 bytes (0061H-0063H)


class CommandHandler:
    def __init__(self):
        self.db = ScaleDatabase()
        self.db.update_total_sales_from_plu()
        self.status_byte = 0b00000000  # Байт состояния
        self.current_state = {
            'overload': False,
            'tare_mode': False,
            'zero_weight': True,
            'stable': True,
            'weight': 0,
            'price': 0,
            'total': 0,
            'current_plu': 0
        }

    def handle_command(self, command: bytes, data: bytes) -> bytes:
        logging.info(f"Обработка команды: {command.hex().upper() if command else 'Нет команды'}")
        try:
            if command == b'\x80':
                return self._handle_ready()
            elif command == b'\x81':
                return self._handle_read_plu(data)
            elif command == b'\x82':
                return self._handle_write_plu(data)
            elif command == b'\x83':
                return self._handle_read_message(data)
            elif command == b'\x84':
                return self._handle_write_message(data)
            elif command == b'\x85':
                return self._handle_get_all_sales_count()
            elif command == b'\x86':
                return self._handle_delete_all_sales_count() 
            elif command == b'\x87':
                return 0
                return self._handle_set_borders_update(data)
            elif command == b'\x88':
                return 0
                return self._handle_delete_borders_update(data)
            elif command == b'\x89':
                return self._handle_read_state()
            elif command == b'\x8A':
                return self._handle_write_user_settings(data) # system
            elif command == b'\x8B':
                return self._handle_programming_sale_keys(data)
            elif command == b'\x8C':
                return self._handle_write_logo2(data)
            elif command == b'\x8D':
                return self._handle_delete_plu(data)
            elif command == b'\x8E':
                return self._handle_delete_message(data)
            elif command == b'\x8F':
                return 0
                return self._handle_write_font_display(data) # system
            elif command == b'\x90':
                return 0
                return self._handle_write_texts_display(data) # system
            elif command == b'\x91':
                return 0
                return self._handle_write_keyboard_layout(data) # system
            elif command == b'\x92':
                return self._handle_delete_sales_count_plu(data)
            elif command == b'\x93':
                return 0
                return self._handle_write_logo(data) 
            elif command == b'\x94':
                return 0
                return self._handle_write_strings_marketing(data)
            elif command == b'\x95':
                return self._handle_read_user_settings()
            elif command == b'\x96':
                return self._handle_read_binded_plu_number(data)
            elif command == b'\x97':
                return self._handle_read_logo2()
            elif command == b'\x98':
                return 0
                return self._handle_read_strings_marketing()
            elif command == b'\x99':
                return 0
                return self._handle_write_real_date(data) # в формате <Д><Д><М><М><Г><Г>
            elif command == b'\x9A':
                return 0
                return self._handle_write_real_time(data) # в формате <Ч><Ч><М><М><С><С>
            elif command == b'\x9B':
                return self._handle_read_factory_settings()
            elif command == b'\x9C':
                return 0
                return self._handle_write_texts_of_service(data) # system Запись текстов служебных надписей, печатаемых на этикетке
            elif command == b'\x9D':
                return 0
                return self._handle_write_user_formats(data) # system Запись спроектированных пользователем форматов этикеток.
            else:
                return b'\xEE'
        except Exception as e:
            return b'\xEE'

    def _handle_ready(self) -> bytes:
        return b''
    
    #region Состояние весов
    def _handle_read_state(self) -> bytes:
        """
        Генерирует и возвращает 15 байт текущего состояния весов согласно протоколу.
        """
        # Генерируем случайные значения для всех полей
        weight = random.randint(-200, 20000)  # Вес в граммах (может быть отрицательным)
        price = random.randint(1000, 99999)   # Цена в копейках за кг (например, 10.00 - 999.99 руб)
        total = abs(weight) * price // 1000   # Стоимость (копеек), для примера
        current_plu = random.randint(1, 4000) # Номер PLU

        # Генерируем биты состояния
        status_byte = 0
        if abs(weight) > 15000:
            status_byte |= 0b00000001  # Перегрузка
        # Бит 1 не используется
        if random.choice([True, False]):
            status_byte |= 0b00000100  # Режим выборки тары
        if weight == 0:
            status_byte |= 0b00001000  # Нулевой вес
        # Бит 4 не используется
        if random.choice([True, False]):
            status_byte |= 0b00100000  # Двухдиапазонный режим
        status_byte |= 0b01000000      # Вес стабильный (пусть всегда стабилен)
        if weight < 0:
            status_byte |= 0b10000000  # Минус

        # Собираем 15 байт
        state = bytearray(15)
        state[0] = status_byte
        state[1:3] = abs(weight).to_bytes(2, 'little')
        state[3:7] = price.to_bytes(4, 'little')
        state[7:11] = total.to_bytes(4, 'little')
        state[11:15] = current_plu.to_bytes(4, 'little')
        
        # self.current_state = {
        #     'overload': bool(status_byte & 0b00000001),
        #     'tare_mode': bool(status_byte & 0b00000100),
        #     'zero_weight': bool(status_byte & 0b00001000),
        #     'dual_range': bool(status_byte & 0b00100000),
        #     'stable': bool(status_byte & 0b01000000),
        #     'weight': weight,
        #     'price': price,
        #     'total': total,
        #     'current_plu': current_plu
        # }

        return bytes(state)
    #endregion

    # region Работа с PLU
    def _handle_read_plu(self, data: bytes) -> bytes:
        """
        Обработка команды чтения PLU.
        Ожидает 4 байта: номер PLU в little-endian (например, b'\x01\x00\x00\x00' для id=1).
        Возвращает 100 байт: 83 байта основной зоны + 17 байт read-only зоны.
        """
        if len(data) < 4:
            return b'\xEE'
        plu_id = unpack('<I', data[:4])[0]
        plu = self.db.get_plu(plu_id)
        logging.info(f"Чтение PLU: {plu_id}, данные: {plu}")
        if not plu:
            return b'\xEE'

        # Если все поля кроме id пустые/нулевые — возвращаем id и нули
        is_empty = (
            (plu.get('code') in (b'\x00'*6, b'', None)) and
            (not plu.get('name1')) and
            (not plu.get('name2')) and
            (plu.get('price', 0) == 0) and
            (plu.get('expiry_date') in (b'\x00'*3, b'', None)) and
            (plu.get('tare', 0) == 0) and
            (plu.get('group_code') in (b'\x00'*6, b'', None)) and
            (plu.get('message_id', 0) == 0)
        )

        if is_empty:
            result = bytearray(100)
            result[0:4] = pack('<I', plu_id)
            return bytes(result)

        # Формируем 83 байта основной зоны
        result = bytearray(83)
        result[0:4] = pack('<I', plu['id'])
        result[4:10] = plu['code'] if isinstance(plu['code'], bytes) else bytes(plu['code'], 'ascii')
        result[10:38] = plu['name1'].encode('cp1251', errors='ignore')[:28].ljust(28, b'\x00')
        result[38:66] = plu['name2'].encode('cp1251', errors='ignore')[:28].ljust(28, b'\x00')
        result[66:70] = pack('<I', plu['price'])
        
        expiry_bytes = plu.get('expiry_date', b'\x00\x00\x00')
        if len(expiry_bytes) < 3:
            expiry_bytes = expiry_bytes.ljust(3, b'\x00')
        else:
            expiry_bytes = expiry_bytes[:3]
        result[70:73] = expiry_bytes
        result[73:75] = pack('<H', plu['tare'])
        result[75:81] = plu['group_code'] if isinstance(plu['group_code'], bytes) else bytes(plu['group_code'], 'ascii')
        result[81:83] = pack('<H', plu['message_id'])

        # Формируем 17 байт read-only зоны
        readonly = bytearray(17)
        # last_reset: (sec, min, hour, day, month, year)
        if plu.get('last_reset'):
            readonly[0:6] = bytes(plu['last_reset'])[:6].ljust(6, b'\x00')
        else:
            readonly[0:6] = bytes(6)
        readonly[6:10] = pack('<I', plu.get('total_sum', 0))
        readonly[10:14] = pack('<I', plu.get('total_weight', 0))
        readonly[14:17] = pack('<I', plu.get('sales_count', 0))[0:3]

        # Гарантируем, что итоговый размер ровно 100 байт
        result = result[:83]
        readonly = readonly[:17]
        return bytes(result + readonly)

    def _handle_write_plu(self, data: bytes) -> bytes:
        logging.debug(f"Данные команды: {data.hex().upper() if data else 'Нет данных'}")
        if len(data) != 83:
            logging.error(f"Invalid PLU length: {len(data)}")
            return b'\xEE'
        try:
            plu_data = {
                'id': unpack('<I', data[0:4])[0],
                'code': data[4:10],
                'name1' : data[10:38].decode('cp1251', errors='ignore').rstrip('\x00'),
                'name2' : data[38:66].decode('cp1251', errors='ignore').rstrip('\x00'),
                'price': unpack('<I', data[66:70])[0],
                'expiry_date': data[70:73],
                'tare': unpack('<H', data[73:75])[0],
                'group_code': data[75:81],
                'message_id': unpack('<H', data[81:83])[0]
            }
            
            self.db.upsert_plu(plu_data)
            return b''

        except Exception as e:
            logging.error(f"Write PLU error: {str(e)}")
            return b'\xEE'

    def _handle_delete_plu(self, data: bytes) -> bytes:
        try:
            plu_id = unpack('<I', data[:4])[0]
            success = self.db.clear_plu(plu_id)
            return b'' if success else b'\xEE'
        except Exception as e:
            logging.error(f"Delete PLU error: {str(e)}")
            return b'\xEE'
    
    def parse_name(raw: bytes) -> tuple:
        logo_num = raw[25] if raw[24] == 0 else None
        cert_code = (raw[27] + raw[26] + raw[25] + raw[24]) if logo_num == 1 else ""
        name = raw[:24].decode('ascii').rstrip('\x00')
        return (name, logo_num, cert_code)

    def validate_plu(plu: PLU):
        assert 0 <= plu.plu_number <= 4000, "Invalid PLU number"
        assert len(plu.item_code) == 6, "Item code must be 6 digits"
        assert plu.price <= 999999, "Price exceeds maximum"

    def _handle_delete_sales_count_plu(self, data: bytes) -> bytes:
        if len(data) < 4:
            return b'\xEE'
        plu_id = unpack('<I', data[:4])[0]
        success = self.db.reset_plu_totals(plu_id)
        return b'' if success else b'\xEE'
    # endregion

    # region Общий итог продаж
    def _handle_get_all_sales_count(self) -> bytes:
        t = self.db.get_total_sales()
        resp = b''
        resp += t['mileage'].to_bytes(4, 'little')
        resp += t['label_count'].to_bytes(4, 'little')
        resp += t['total_sum'].to_bytes(4, 'little')
        resp += t['sales_count'].to_bytes(3, 'little')
        resp += t['total_weight'].to_bytes(4, 'little')
        resp += t['plu_sum'].to_bytes(4, 'little')
        resp += t['plu_sales_count'].to_bytes(3, 'little')
        resp += t['plu_weight'].to_bytes(4, 'little')
        resp += t['last_reset_bcd'] if t['last_reset_bcd'] else b'\x00'*6
        resp += t['free_plu'].to_bytes(2, 'little')
        resp += t['free_msg'].to_bytes(2, 'little')
        return resp[:40]

    def _handle_delete_all_sales_count(self) -> bytes:
        return b'' if self.db.reset_total_sales() else b'\xEE'
    # endregion

    # region Работа с сообщениями
    def _handle_read_message(self, data: bytes) -> bytes:
        msg_id = unpack('<H', data[:2])[0]
        msg = self.db.get_message(msg_id)
        if not msg:
            return b'\xEE'
        
        response = bytearray(400)
        content = msg.encode('cp1251', errors='replace')
        for i in range(8):
            start = i * 50
            end = start + 50
            response[start:end] = content[start:end].ljust(50, b'\x00')
        return bytes(response)

    def _handle_write_message(self, data: bytes) -> bytes:
        msg_num = unpack('<H', data[:2])[0]
        #content = data[2:402].decode('ascii').replace('\x00', '')
        content = data[2:402].decode('cp1251', errors='replace').replace('\x00', '')
        try:
            success = self.db.insert_message(msg_num, content)
            return b'' if success else b'\xEE'
        except Exception as e:
            logging.error(f"Write message error: {str(e)}")
            return b'\xEE'

    def _handle_delete_message(self, data: bytes) -> bytes:
        msg_num = unpack('<H', data)[0]
        success = self.db.delete_message(msg_num)
        return b'' if success else b'\xEE'
    # endregion

    # region Логотипы
    def _handle_write_logo2(self, data: bytes) -> bytes:
        return
        try:
            logo_header = data[:8]
            logo_data = data[8:520]
            cert_code = data[520:524].decode('ascii')
            
            logo_info = decode_logo_header(logo_header)
            
            with self.db._get_connection() as conn:
                conn.execute('''INSERT OR REPLACE INTO logos 
                            (id, data, cert_code) VALUES (2, ?, ?)''',
                            (logo_data, cert_code))
            return b''
        except Exception as e:
            logging.error(f"Logo write error: {str(e)}")
            return b'\xEE'

    def _handle_write_logo2(self, data: bytes) -> bytes:
        """Обработка записи логотипа (512 байт данных + 4 байта сертификата)"""
        try:
            logo_id = 2
            logo_data = data[:512]
            cert_code = data[512:516].decode('ascii')
            
            # Валидация данных
            if len(logo_data) != 512 or len(cert_code) != 4:
                return b'\xEE'
                
            # Сохранение в БД
            with self.db._get_connection() as conn:
                conn.execute('''INSERT OR REPLACE INTO logos 
                            (id, data, cert_code) VALUES (?, ?, ?)''',
                            (logo_id, logo_data, cert_code))
                
            return b''
        except Exception as e:
            logging.error(f"Logo write error: {str(e)}")
            return b'\xEE'
    # endregion

    # region Настройки пользователя
    def _handle_read_user_settings(self) -> bytes:
        settings = self.db.get_user_settings()
        dept_no = settings.get('dept_no', 0)
        dept_no_bytes = bytes(int(d) for d in f"{dept_no:03d}")

        result = (
            dept_no_bytes +  # 3 байта
            settings.get('label_format', 1).to_bytes(1, 'little') +
            settings.get('barcode_format', 0).to_bytes(1, 'little') +
            settings.get('adjst', 1).to_bytes(1, 'little') +
            settings.get('print_features', 0).to_bytes(1, 'little') +
            settings.get('auto_print_weight', 0).to_bytes(2, 'little')
        )
        return result[:9]

    def _handle_write_user_settings(self, data: bytes) -> bytes:
        if len(data) != 9:
            return b'\xEE'
        settings = {
            'dept_no': data[0],
            'label_format': data[1],
            'barcode_format': data[2],
            'adjst': data[3],
            'print_features': data[4],
            'auto_print_weight': int.from_bytes(data[5:7], 'little')
        }
        ok = self.db.set_user_settings(settings)
        return b'' if ok else b'\xEE'
    # endregion

    # region Заводские настройки
    def _handle_read_factory_settings(self) -> bytes:
        settings = self.db.get_factory_settings()
        # Формируем 13 байт согласно протоколу:
        # 2 байта max_weight, 1 байт dec_point_weight, 1 байт dec_point_price, 1 байт dec_point_sum,
        # 1 байт dual_range, 2 байта weight_step_upper, 2 байта weight_step_lower,
        # 2 байта price_weight, 1 байт round_sum, 2 байта tare_limit
        try:
            result = (
                settings['max_weight'].to_bytes(2, 'little') +
                settings['dec_point_weight'].to_bytes(1, 'little') +
                settings['dec_point_price'].to_bytes(1, 'little') +
                settings['dec_point_sum'].to_bytes(1, 'little') +
                settings['dual_range'].to_bytes(1, 'little') +
                settings['weight_step_upper'].to_bytes(2, 'little') +
                settings['weight_step_lower'].to_bytes(2, 'little') +
                settings['price_weight'].to_bytes(2, 'little') +
                settings['round_sum'].to_bytes(1, 'little') +
                settings['tare_limit'].to_bytes(2, 'little')
            )
            return result[:13]
        except Exception as e:
            logging.error(f"Pack factory settings error: {str(e)}")
            return b'\xEE'
    # endregion
    
    # region Логотипы
    def _handle_read_logo2(self) -> bytes:
        try:
            logo = self.db.get_logo(2)
            if logo and len(logo) == 512:
                return logo
            else:
                return b'\x00' * 512  # Пустой логотип
        except Exception as e:
            logging.error(f"Read logo2 error: {str(e)}")
            return b'\xEE'
        
    def _handle_write_logo(self, data: bytes) -> bytes:
        try:
            if len(data) != 384:
                return b'\xEE'
            with self.db._get_connection() as c:
                c.execute('''INSERT OR REPLACE INTO logos (id, data, cert_code) VALUES (?, ?, ?)''',
                        (1, data, '0000'))
            return b''
        except Exception as e:
            logging.error(f"Write logo_roste error: {str(e)}")
            return b'\xEE'
    # endregion    

    # region Другие команды
    def _handle_set_borders_update(self, data: bytes) -> bytes:
        start = unpack('<I', data[:4])[0]
        end = unpack('<I', data[4:8])[0]
        
        if not (0 <= start <= end <= 4000):
            return b'\xEE'
        
        try:
            with self.db._get_connection() as conn:
                conn.execute('DELETE FROM update_borders')
                conn.execute('INSERT INTO update_borders (start, end) VALUES (?, ?)',
                            (start, end))
            return b''
        except Exception as e:
            logging.error(f"Borders error: {str(e)}")
            return b'\xEE'

    def _handle_programming_sale_keys(self, data: bytes) -> bytes:
        plu_num = unpack('<I', data[:4])[0]
        key_num = data[4]
        logging.info(f"Получена команда привязки: key_num={key_num}, plu_num={plu_num}")
        if not (1 <= key_num <= 54):
            return b'\xEE'
        
        try:
            if not self.db.bind_plu_to_key(key_num, plu_num):
                return b'\xEE'
            return b''
        except Exception as e:
            logging.error(f"Price key error: {str(e)}")
            return b'\xEE'
        
    def _handle_read_binded_plu_number(self, data: bytes) -> bytes:
        if len(data) < 1:
            return b'\xEE'
        key_num = data[0]
        if not (1 <= key_num <= 54):
            return b'\xEE'
        try:
            plu_id = self.db.get_plu_by_key(key_num)
            if plu_id is None:
                # Возвращаем 4 байта нулей, если не привязано
                return (0).to_bytes(4, 'little')
            return pack('<I', plu_id)
        except Exception as e:
            logging.error(f"Read binded PLU error: {str(e)}")
            return b'\xEE'
    # endregion
    
