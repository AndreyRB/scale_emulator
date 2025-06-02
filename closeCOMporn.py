import serial

import serial.tools.list_ports

port_name = 'COM4'

# Проверяем, существует ли порт
ports = [port.device for port in serial.tools.list_ports.comports()]
if port_name not in ports:
    print(f"Порт {port_name} не найден.")
else:
    try:
        # Пробуем открыть и сразу закрыть порт
        ser = serial.Serial(port_name)
        ser.close()
        print(f"Порт {port_name} успешно закрыт.")
    except serial.SerialException as e:
        print(f"Не удалось закрыть порт {port_name}: {e}")