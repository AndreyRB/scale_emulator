# database.py
import os
import sqlite3
from contextlib import contextmanager
import logging

class ScaleDatabase:
    def __init__(self):
        db_path = os.path.join('.', 'scale_emulator', 'emulator', 'db', 'scale.db')
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        logging.info(f"Инициализация БД по пути: {self.db_path}")
        self._init_db()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception as e:
            conn.rollback()
            logging.error(f"DB error: {str(e)}")
            raise
        finally:
            cursor.close()
            conn.close()

    def _init_db(self):
        try:
            with self._get_connection() as c:
                # PLU данные
                c.execute('''CREATE TABLE IF NOT EXISTS plu (
                    id INTEGER PRIMARY KEY,
                    code BLOB CHECK(length(code) = 6),
                    name1 TEXT CHECK(length(name1) <= 28),
                    name2 TEXT CHECK(length(name2) <= 28),
                    price INTEGER CHECK(price BETWEEN 0 AND 999999),
                    expiry_date BLOB CHECK(length(expiry_date) = 3),
                    tare INTEGER,
                    group_code BLOB CHECK(length(group_code) = 6),
                    message_id INTEGER,
                    last_reset BLOB CHECK(length(last_reset) = 6),
                    total_sum INTEGER DEFAULT 0,
                    total_weight INTEGER DEFAULT 0,
                    sales_count INTEGER DEFAULT 0
                )''')

                c.execute('''CREATE TABLE IF NOT EXISTS total_sales (
                    id INTEGER PRIMARY KEY CHECK(id = 1),
                    mileage INTEGER DEFAULT 0,           -- 4 байта
                    label_count INTEGER DEFAULT 0,       -- 4 байта
                    total_sum INTEGER DEFAULT 0,         -- 4 байта
                    sales_count INTEGER DEFAULT 0,       -- 3 байта
                    total_weight INTEGER DEFAULT 0,      -- 4 байта
                    plu_sum INTEGER DEFAULT 0,           -- 4 байта
                    plu_sales_count INTEGER DEFAULT 0,   -- 3 байта
                    plu_weight INTEGER DEFAULT 0,        -- 4 байта
                    last_reset_bcd BLOB,                 -- 6 байт BCD
                    free_plu INTEGER DEFAULT 4000,       -- 2 байта
                    free_msg INTEGER DEFAULT 4000        -- 2 байта
                    )
                ''')

                # Сообщения
                c.execute('''CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY,
                    content TEXT CHECK(length(content) <= 400)
                )''')

                # Логотипы
                c.execute('''CREATE TABLE IF NOT EXISTS logos (
                    id INTEGER PRIMARY KEY CHECK(id IN (1, 2)),
                    data BLOB CHECK(length(data) IN (384, 512)),
                    cert_code TEXT CHECK(length(cert_code) = 4)
                )''')

                # Таблица пользовательских настроек
                c.execute('''
                    CREATE TABLE IF NOT EXISTS user_settings (
                        id INTEGER PRIMARY KEY CHECK(id = 1),
                        dept_no INTEGER,
                        label_format INTEGER,
                        barcode_format INTEGER,
                        adjst INTEGER,
                        print_features INTEGER,
                        auto_print_weight INTEGER
                    )
                ''')

                # Таблица заводских настроек
                c.execute('''
                    CREATE TABLE IF NOT EXISTS factory_settings (
                        id INTEGER PRIMARY KEY CHECK(id = 1),
                        max_weight INTEGER,
                        dec_point_weight INTEGER,
                        dec_point_price INTEGER,
                        dec_point_sum INTEGER,
                        dual_range INTEGER,
                        weight_step_upper INTEGER,
                        weight_step_lower INTEGER,
                        price_weight INTEGER,
                        round_sum INTEGER,
                        tare_limit INTEGER
                    )
                ''')

                c.execute('''CREATE TABLE IF NOT EXISTS price_keys (
                    key_num INTEGER PRIMARY KEY CHECK(key_num BETWEEN 1 AND 54),
                    plu_id INTEGER REFERENCES plu(id)
                )''')
        except Exception as e:
            logging.critical(f"Ошибка инициализации БД: {str(e)}")
            raise

    # region PLU Operations
    def get_plu(self, plu_id: int) : #-> Optional[dict]
        with self._get_connection() as c:
            c.execute('SELECT * FROM plu WHERE id = ?', (plu_id,))
            row = c.fetchone()
            return dict(row) if row else None

    def upsert_plu(self, plu_data: dict) -> bool:
        try:
            with self._get_connection() as c:
                c.execute('''INSERT OR REPLACE INTO plu 
                            (id, code, name1, name2, price, 
                            expiry_date, tare, group_code, message_id)
                            VALUES 
                            (:id, :code, :name1, :name2, :price,
                            :expiry_date, :tare, :group_code, :message_id)''',
                            {
                                'id': plu_data['id'],
                                'code': plu_data['code'],
                                'name1': plu_data['name1'],
                                'name2': plu_data['name2'],
                                'price': plu_data['price'],
                                'expiry_date': plu_data['expiry_date'],
                                'tare': plu_data['tare'],
                                'group_code': plu_data['group_code'],
                                'message_id': plu_data['message_id']
                            })
                return c.rowcount > 0
        except sqlite3.IntegrityError as e:
            logging.error(f"PLU integrity error: {str(e)}")
            return False

    def clear_plu(self, plu_id: int) -> bool:
        """Очищает данные PLU, но оставляет строку в таблице."""
        with self._get_connection() as c:
            # Сбросить все поля, кроме id, к значениям по умолчанию (NULL или 0)
            c.execute('''
                UPDATE plu SET
                    code = NULL,
                    name1 = NULL,
                    name2 = NULL,
                    price = NULL,
                    expiry_date = NULL,
                    tare = NULL,
                    group_code = NULL,
                    message_id = NULL,
                    last_reset = NULL,
                    total_sum = 0,
                    total_weight = 0,
                    sales_count = 0
                WHERE id = ?
            ''', (plu_id,))
            return c.rowcount > 0

    def search_plu(self, search_term: str) -> list:
        """Поиск по названию товара"""
        with self._get_connection() as c:
            c.execute('''
                SELECT * FROM plu 
                WHERE name1 LIKE ? OR name2 LIKE ?
            ''', (f'%{search_term}%', f'%{search_term}%'))
            return [dict(row) for row in c.fetchall()]

    def get_plu_count(self) -> int:
        """Получить общее количество записей"""
        with self._get_connection() as c:
            return c.execute('SELECT COUNT(*) FROM plu').fetchone()[0]
        
    def reset_plu_totals(self, plu_id: int) -> bool:
        """Обнуляет total_sum, total_weight, sales_count для PLU с заданным id."""
        with self._get_connection() as c:
            c.execute('''
                UPDATE plu SET
                    total_sum = 0,
                    total_weight = 0,
                    sales_count = 0
                WHERE id = ?
            ''', (plu_id,))
            return c.rowcount > 0
    # endregion

    # region Message Operations
    def get_message(self, msg_id: int) : #-> Optional[str]
        with self._get_connection() as c:
            c.execute('SELECT content FROM messages WHERE id = ?', (msg_id,))
            row = c.fetchone()
            return row['content'] if row else None

    def insert_message(self, msg_id: int, content: str) -> bool:
        try:
            with self._get_connection() as c:
                c.execute('INSERT OR REPLACE INTO messages VALUES (?, ?)', 
                         (msg_id, content))
                return c.rowcount > 0
        except sqlite3.IntegrityError:
            return False

    def delete_message(self, msg_id: int) -> bool:
        with self._get_connection() as c:
            c.execute('DELETE FROM messages WHERE id = ?', (msg_id,))
            return c.rowcount > 0
    # endregion

    # region Logo Operations
    def get_logo(self, logo_id: int): # -> Optional[bytes]
        with self._get_connection() as c:
            c.execute('SELECT data FROM logos WHERE id = ?', (logo_id,))
            row = c.fetchone()
            return row['data'] if row else None

    def upsert_logo(self, logo_id: int, data: bytes, cert_code: str) -> bool:
        try:
            with self._get_connection() as c:
                c.execute('''INSERT OR REPLACE INTO logos 
                          VALUES (?, ?, ?)''', 
                          (logo_id, data, cert_code))
                return c.rowcount > 0
        except sqlite3.IntegrityError:
            return False
    # endregion

    # region User Settings
    def get_user_settings(self) -> dict:
        with self._get_connection() as c:
            c.execute('SELECT * FROM user_settings WHERE id = 1')
            row = c.fetchone()
            if row:
                return dict(row)
            # Значения по умолчанию
            return {
                'dept_no': 0,
                'label_format': 1,
                'barcode_format': 0,
                'adjst': 1,
                'print_features': 0,
                'auto_print_weight': 0
            }

    def set_user_settings(self, settings: dict) -> bool:
        try:
            with self._get_connection() as c:
                c.execute('''
                    INSERT OR REPLACE INTO user_settings
                    (id, dept_no, label_format, barcode_format, adjst, print_features, auto_print_weight)
                    VALUES (1, :dept_no, :label_format, :barcode_format, :adjst, :print_features, :auto_print_weight)
                ''', settings)
                return c.rowcount > 0
        except Exception as e:
            logging.error(f"DB: set_user_settings error: {str(e)}")
            return False
    # endregion

    # region Factory Settings
    def get_factory_settings(self) -> dict:
        with self._get_connection() as c:
            c.execute('SELECT * FROM factory_settings WHERE id = 1')
            row = c.fetchone()
            if row:
                return dict(row)
            # Значения по умолчанию
            return {
                'max_weight': 100,
                'dec_point_weight': 0,
                'dec_point_price': 0,
                'dec_point_sum': 0,
                'dual_range': 0,
                'weight_step_upper': 1,
                'weight_step_lower': 1,
                'price_weight': 0,
                'round_sum': 0,
                'tare_limit': 0
            }
    # endregion
    
    # region Total Sales Operations
    def get_total_sales(self) -> dict:
        with self._get_connection() as c:
            row = c.execute('SELECT * FROM total_sales WHERE id = 1').fetchone()
            if row:
                return dict(row)
            # Если нет строки — вернуть значения по умолчанию
            return {
                'mileage': 0,
                'label_count': 0,
                'total_sum': 0,
                'sales_count': 0,
                'total_weight': 0,
                'plu_sum': 0,
                'plu_sales_count': 0,
                'plu_weight': 0,
                'last_reset_bcd': b'\x00'*6,
                'free_plu': 4000,
                'free_msg': 4000
            }

    def set_total_sales(self, values: dict) -> bool:
        try:
            with self._get_connection() as c:
                c.execute('''
                    INSERT OR REPLACE INTO total_sales
                    (id, mileage, label_count, total_sum, sales_count, total_weight,
                    plu_sum, plu_sales_count, plu_weight, last_reset_bcd, free_plu, free_msg)
                    VALUES (1, :mileage, :label_count, :total_sum, :sales_count, :total_weight,
                            :plu_sum, :plu_sales_count, :plu_weight, :last_reset_bcd, :free_plu, :free_msg)
                ''', values)
            return True
        except Exception as e:
            logging.error(f"DB: set_total_sales error: {str(e)}")
            return False

    def reset_total_sales(self):
        from datetime import datetime
        now = datetime.now()
        # BCD-пакет: сек, мин, час, день, мес, год
        bcd = bytes([
            ((now.second // 10) << 4) | (now.second % 10),
            ((now.minute // 10) << 4) | (now.minute % 10),
            ((now.hour // 10) << 4) | (now.hour % 10),
            ((now.day // 10) << 4) | (now.day % 10),
            ((now.month // 10) << 4) | (now.month % 10),
            ((now.year % 100 // 10) << 4) | (now.year % 10)
        ])
        values = {
            'mileage': 0,
            'label_count': 0,
            'total_sum': 0,
            'sales_count': 0,
            'total_weight': 0,
            'plu_sum': 0,
            'plu_sales_count': 0,
            'plu_weight': 0,
            'last_reset_bcd': bcd,
            'free_plu': 4000,
            'free_msg': 1000
        }
        return self.set_total_sales(values)

    def calc_total_sales_from_plu(self) -> dict:
        """
        Подсчитывает общие итоги продаж
        """
        with self._get_connection() as c:
            row = c.execute('''
                SELECT 
                    SUM(total_sum) as total_sum,
                    SUM(total_weight) as total_weight,
                    SUM(sales_count) as sales_count,
                    COUNT(*) as plu_count
                FROM plu
            ''').fetchone()
            msg_row = c.execute('SELECT COUNT(*) as msg_count FROM messages').fetchone()
            # Получаем дату последнего сброса
            last_reset_row = c.execute('SELECT last_reset_bcd FROM total_sales WHERE id = 1').fetchone()
            return {
                'total_sum': row['total_sum'] or 0,
                'total_weight': row['total_weight'] or 0,
                'sales_count': row['sales_count'] or 0,
                'plu_count': row['plu_count'] or 0,
                'free_plu': 4000 - (row['plu_count'] or 0),
                'free_msg': 1000 - (msg_row['msg_count'] or 0),
                'last_reset_bcd': last_reset_row['last_reset_bcd'] if last_reset_row else b'\x00'*6
            }

    def update_total_sales_from_plu(self):
        """
        Пересчитывает общие итоги продаж по всем PLU и обновляет таблицу total_sales.
        """
        totals = self.calc_total_sales_from_plu()
        values = {
            'mileage': 0,
            'label_count': 0,
            'total_sum': totals['total_sum'],
            'sales_count': totals['sales_count'],
            'total_weight': totals['total_weight'],
            'plu_sum': totals['total_sum'],
            'plu_sales_count': totals['sales_count'],
            'plu_weight': totals['total_weight'],
            'last_reset_bcd': totals['last_reset_bcd'],
            'free_plu': totals['free_plu'],
            'free_msg': totals['free_msg']
        }
        self.set_total_sales(values)
    #endregion

    # region Price Keys Operations
    def bind_plu_to_key(self, key_num: int, plu_id: int) -> bool:
        with self._get_connection() as c:
            # Проверка существования PLU
            if not c.execute('SELECT * FROM plu WHERE id = ?', (plu_id,)).fetchone():
                return False
            
            c.execute('INSERT OR REPLACE INTO price_keys VALUES (?, ?)', 
                     (key_num, plu_id))
            return c.rowcount > 0

    def get_plu_by_key(self, key_num: int):
        with self._get_connection() as c:
            c.execute('SELECT plu_id FROM price_keys WHERE key_num = ?', (key_num,))
            row = c.fetchone()
            if row is not None:
                return row['plu_id']
            return None
    # endregion