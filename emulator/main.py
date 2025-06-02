import sys
import time
import serial
import logging
import random
from threading import Thread
from struct import pack
import signal
from .commands import CommandHandler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

class ScaleEmulator:
    def __init__(self, port='COM4', baudrate=9600):
        signal.signal(signal.SIGINT, self._handle_signal)  # Ctrl+C
        signal.signal(signal.SIGTERM, self._handle_signal) # Завершение процесса

        self.port = port
        self.baudrate = baudrate
        self.command_handler = CommandHandler()
        self.ser = None
        self.running = False
        self.current_weight = 0
        self.status_byte = 0b00000000

    def _handle_signal(self, signum, frame):
        self.stop()
        sys.exit(0)

    def parse_command(self, raw_data: bytes) -> tuple:
        try:
            if not raw_data:
                return (None, None)
            
            cmd = raw_data[0]
            data = raw_data[1:]  # Все байты после команды
            return bytes([cmd]), data
        
        except Exception as e:
            logging.error(f"Ошибка парсинга команды: {str(e)}")
            return (None, None)

    def _handle_command(self, command: bytes):
        """Обработка входящих команд"""
        if command:
            cmd, data = self.parse_command(command)
            if cmd is not None:
                logging.info(f"Получена команда: {cmd.hex().upper()}")
                logging.debug(f"Данные команды: {data.hex().upper() if data else 'Нет данных'}")
                return self.command_handler.handle_command(cmd, data)  # Передаем и cmd, и data
        return b'\xEE'  # Возвращаем ошибку по умолчанию

    def _connection_thread(self):
        while self.running:
            try:
                if self.ser.in_waiting > 0:
                    raw_data = self.ser.read(self.ser.in_waiting)
                    logging.debug(f"Получены сырые данные: {raw_data.hex()}")
                    response = self._handle_command(raw_data)
                    if response:
                        self.ser.write(response)
                        logging.info(f"Отправлен ответ: {response.hex().upper()}")
                    # После любого ответа отправляем байт готовности
                    time.sleep(0.2)
                    self.ser.write(b'\x80')
                    logging.info("Весы готовы к следующей команде")

            except Exception as e:
                logging.error(f"Ошибка потока: {str(e)}")
                self.stop()

    def start(self):
        """Запуск эмулятора"""
        try:
            self.ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=8,
                parity='N',
                stopbits=1,
                timeout=1
            )
            # Сразу после открытия порта отправляем байт готовности
            self.ser.write(b'\x80')
            logging.info("Весы готовы к первой команде (байт готовности отправлен)")
            self.running = True
            Thread(target=self._connection_thread, daemon=True).start()
            logging.info(f"Эмулятор запущен на {self.port}")
            
            # Бесконечный цикл для работы в фоне
            while self.running:
                time.sleep(1)

        except KeyboardInterrupt:
            self.stop()
        except Exception as e:
            logging.error(f"Ошибка запуска: {str(e)}")
        finally:
            self.stop()

    def stop(self):
        """Остановка эмулятора"""
        if self.running:
            self.running = False
            if self.ser and self.ser.is_open:
                self.ser.close()
            logging.info("Эмулятор остановлен")

if __name__ == "__main__":
    emulator = ScaleEmulator(port='COM4')
    emulator.start()