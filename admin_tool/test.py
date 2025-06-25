from admin_db import AdminDatabase

if __name__ == "__main__":
    db = AdminDatabase()
    username = input("Введите имя пользователя: ")
    password = input("Введите пароль: ")
    if db.add_user(username, password):
        print("Пользователь успешно добавлен!")
    else:
        print("Ошибка: пользователь не добавлен (возможно, такой пользователь уже есть).")