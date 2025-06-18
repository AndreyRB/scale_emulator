import os
import sqlite3
import random
from datetime import datetime, timedelta
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def datetime_to_bcd(dt: datetime) -> bytes:
    """Конвертирует datetime в 6-байтовый BCD-формат"""
    return bytes([
        ((dt.second // 10) << 4) | (dt.second % 10),
        ((dt.minute // 10) << 4) | (dt.minute % 10),
        ((dt.hour // 10) << 4) | (dt.hour % 10),
        ((dt.day // 10) << 4) | (dt.day % 10),
        ((dt.month // 10) << 4) | (dt.month % 10),
        ((dt.year % 100 // 10) << 4) | (dt.year % 10)
    ])

def generate_test_data():
    """Генерирует тестовые данные для PLU и сообщений"""
    # Генерируем 10 сообщений
    messages = [
        {"id": i + 1, "content": f"Сообщение #{i+1}: {' '.join(['тест'] * random.randint(3, 8))}"}
        for i in range(10)
    ]
    
    # Генерируем 10 товаров
    plu_items = []
    for i in range(1, 11):
        # Случайно выбираем тип срока годности
        expiry_type = random.choice([0, 1])
        
        if expiry_type == 0:  # Дата
            expiry_date = (
                random.randint(1, 28),  # День
                random.randint(1, 12),  # Месяц
                random.randint(23, 25)  # Год (2023-2025)
            )
            expiry_bytes = bytes(expiry_date)
        else:  # Дни
            days = random.randint(1, 365)
            expiry_bytes = bytes([0x00, (days // 100) & 0xFF, days % 100])
        
        # Генерируем дату последнего сброса
        last_reset_dt = datetime.now() - timedelta(days=random.randint(1, 30))
        last_reset_bytes = datetime_to_bcd(last_reset_dt)
        
        plu_items.append({
            "id": i,
            "code": bytes([int(x) for x in ''.join([str(random.randint(0, 9)) for _ in range(6)])]),
            "name1": f"Товар #{i} {' '.join(['тест'] * random.randint(1, 3))}",
            "name2": f"Категория: {' '.join(['кат'] * random.randint(1, 3))}",
            "price": random.randint(100, 10000),  # В копейках
            "expiry_date": expiry_bytes,
            "tare": random.randint(0, 100),
            "group_code": bytes([int(x) for x in ''.join([str(random.randint(0, 9)) for _ in range(6)])]),
            "message_id": random.randint(1, 10),
            "last_reset": last_reset_bytes,  # BCD-формат
            "total_sum": random.randint(1000, 100000),
            "total_weight": random.randint(1000, 100000),
            "sales_count": random.randint(1, 100)
        })
    
    return messages, plu_items

def seed_database():
    """Заполняет базу данных тестовыми данными"""
    db_path = os.path.join('.', 'scale_emulator', 'emulator', 'db', 'scale.db')
    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        
        # Очищаем таблицы перед заполнением
        c.execute("DELETE FROM messages")
        c.execute("DELETE FROM plu")
        
        # Генерируем тестовые данные
        messages, plu_items = generate_test_data()
        
        # Вставляем сообщения
        for msg in messages:
            c.execute(
                "INSERT OR REPLACE INTO messages (id, content) VALUES (?, ?)",
                (msg['id'], msg['content'])
            )
        
        # Вставляем товары
        for item in plu_items:
            c.execute(
                """INSERT OR REPLACE INTO plu (
                    id, code, name1, name2, price, expiry_date, tare, 
                    group_code, message_id, last_reset, total_sum, 
                    total_weight, sales_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item['id'], item['code'], item['name1'], item['name2'],
                    item['price'], item['expiry_date'], item['tare'],
                    item['group_code'], item['message_id'], item['last_reset'],
                    item['total_sum'], item['total_weight'], item['sales_count']
                )
            )
        
        conn.commit()
        logging.info(f"Успешно добавлено: {len(messages)} сообщений и {len(plu_items)} товаров")
        
    except sqlite3.Error as e:
        logging.error(f"Ошибка базы данных: {str(e)}")
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    seed_database()