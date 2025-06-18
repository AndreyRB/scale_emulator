import subprocess
import sys
import time
import logging

import threading

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

def run_process(command, name):
    """Запуск подпроцесса с логированием"""
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
        universal_newlines=True
    )
    logging.info(f"Запущен процесс: {name} (PID: {process.pid})")
    return process

def log_stream(process, prefix):
    for line in iter(process.stdout.readline, ''):
        if line:
            logging.info(f"[{prefix}] {line.strip()}")
        if process.poll() is not None:
            break

if __name__ == "__main__":
    emulator = run_process(
        [sys.executable, "-u", "-m", "scale_emulator.emulator.main"],
        "Эмулятор весов"
    )
    time.sleep(3)
    # admin = run_process(
    #     [sys.executable, "-u", "scale_emulator/admin_tool/admin_guiPyQt.py"],
    #     "Административная панель"
    # )
    admin = run_process(
        [sys.executable, "-u", "scale_emulator/admin_tool/admin_guiFlask2.py"],
        "Flask админка"
    )

    # Запускаем потоки для логирования вывода каждого процесса
    threads = [
        threading.Thread(target=log_stream, args=(emulator, "Эмулятор")),
        threading.Thread(target=log_stream, args=(admin, "Админка")),
    ]
    for t in threads:
        t.start()

    try:
        while any(t.is_alive() for t in threads):
            time.sleep(0.1)
    except KeyboardInterrupt:
        emulator.terminate()
        admin.terminate()
        logging.info("Принудительное завершение")
    except Exception as e:
        emulator.terminate()
        admin.terminate()
        logging.error(f"Ошибка: {str(e)}")
