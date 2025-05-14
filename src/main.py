import telebot
import sqlite3
import json
import configparser
from datetime import datetime
import requests
from typing import Optional, Dict, Any, List, Tuple
import logging
import re

# Конфигурация приложения
config = configparser.ConfigParser()
config.read('config.ini')

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Константы
API_URL = config.get('API', 'URL', fallback='https://api.pro-talk.ru/api/v1.0/ask/ZjpiQl0ZH3BuxE6dXSYnqIOIA2QnrqIc')
BOT_ID = config.getint('API', 'BOT_ID', fallback=25590)
TIMEOUT = config.getint('API', 'TIMEOUT', fallback=30)
MAX_RETRIES = config.getint('API', 'MAX_RETRIES', fallback=3)
TOKEN = config.get('TELEGRAM', 'TOKEN', fallback='7815995188:AAGA8e4dC_Gk1do6-gddvVxKO0ceQUIueUs')

# Инициализация бота
bot = telebot.TeleBot(TOKEN)

# Глобальные переменные для хранения состояния
user_states = {}
search_states = {}


class DatabaseManager:
    """Класс для управления базой данных"""

    def __init__(self, db_name: str = 'companies.db'):
        self.db_name = db_name
        self.init_db()

    def init_db(self):
        """Инициализация структуры базы данных"""
        with sqlite3.connect(self.db_name) as db:
            cursor = db.cursor()
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS companies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                inn TEXT,
                phone TEXT,
                contact_person TEXT,
                email TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')

            cursor.execute('''
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER,
                file_url TEXT NOT NULL,
                file_type TEXT NOT NULL,
                caption TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (company_id) REFERENCES companies(id)
            )
            ''')
            db.commit()

    def save_company(self, company_data: Dict[str, Any]) -> int:
        """Сохранение компании в базу данных"""
        with sqlite3.connect(self.db_name) as db:
            cursor = db.cursor()
            cursor.execute('''
            INSERT INTO companies (name, inn, phone, contact_person, email)
            VALUES (?, ?, ?, ?, ?)
            ''', (
                company_data.get('name'),
                company_data.get('inn'),
                company_data.get('phone'),
                company_data.get('contact_person'),
                company_data.get('email')
            ))

            company_id = cursor.lastrowid
            db.commit()
            return company_id

    def save_file(self, company_id: int, file_url: str, file_type: str, caption: Optional[str] = None):
        """Сохранение файла в базу данных"""
        with sqlite3.connect(self.db_name) as db:
            cursor = db.cursor()
            cursor.execute('''
            INSERT INTO files (company_id, file_url, file_type, caption)
            VALUES (?, ?, ?, ?)
            ''', (company_id, file_url, file_type, caption))
            db.commit()

    def search_company(self, search_type: str, search_value: str) -> List[Tuple]:
        """Поиск компании в базе данных"""
        field_map = {
            'названию': 'name',
            'инн': 'inn',
            'email': 'email'
        }

        field = field_map.get(search_type.lower())
        if not field:
            return []

        with sqlite3.connect(self.db_name) as db:
            cursor = db.cursor()
            cursor.execute(f'''
            SELECT * FROM companies 
            WHERE {field} LIKE ?
            ''', (f'%{search_value}%',))
            return cursor.fetchall()

    def get_all_companies(self) -> List[Tuple]:
        """Получение всех компаний из базы данных"""
        with sqlite3.connect(self.db_name) as db:
            cursor = db.cursor()
            cursor.execute('SELECT * FROM companies ORDER BY created_at DESC')
            return cursor.fetchall()

    def get_company_by_id(self, company_id: int) -> Optional[Tuple]:
        """Получение компании по ID"""
        with sqlite3.connect(self.db_name) as db:
            cursor = db.cursor()
            cursor.execute('SELECT * FROM companies WHERE id = ?', (company_id,))
            return cursor.fetchone()


class APIClient:
    """Класс для работы с API"""

    def __init__(self, base_url: str, bot_id: int):
        self.base_url = base_url
        self.bot_id = bot_id

    def send_request(self, chat_id: int, message_id: int, content: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Отправка запроса к API"""
        payload = {
            "bot_id": self.bot_id,
            "chat_id": f"chat_{chat_id}_{message_id}",
            "message": content if isinstance(content, str) else json.dumps(content)
        }

        headers = {'Content-Type': 'application/json'}

        for attempt in range(MAX_RETRIES):
            try:
                response = requests.post(
                    self.base_url,
                    json=payload,
                    headers=headers,
                    timeout=TIMEOUT
                )
                response.raise_for_status()
                return response.json()
            except (requests.RequestException, requests.Timeout) as e:
                if attempt == MAX_RETRIES - 1:
                    raise
                import time
                time.sleep(2 ** attempt)
        return None


def parse_api_response(response_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Парсинг ответа API в строгом формате:
    Название: название компании
    ИНН: инн компании
    Телефон: телефон
    Контактное лицо: ФИО
    Email: email компании
    """
    if not response_data:
        return None

    text_data = response_data.get('done') or response_data.get('message') or response_data.get('text', '')

    if not text_data:
        return None

    company_data = {}
    lines = text_data.split('\n')

    for line in lines:
        if 'Название:' in line:
            company_data['name'] = line.replace('Название:', '').strip()
        elif 'ИНН:' in line:
            company_data['inn'] = line.replace('ИНН:', '').strip()
        elif 'Телефон:' in line:
            company_data['phone'] = line.replace('Телефон:', '').strip()
        elif 'Контактное лицо:' in line:
            company_data['contact_person'] = line.replace('Контактное лицо:', '').strip()
        elif 'Email:' in line:
            company_data['email'] = line.replace('Email:', '').strip()

    required_fields = ['name', 'inn', 'email']
    if all(field in company_data for field in required_fields):
        return company_data

    return None


def format_company_info(company: Tuple) -> str:
    """Форматирование информации о компании для вывода"""
    return (
        f"Название: {company[1]}\n"
        f"ИНН: {company[2]}\n"
        f"Телефон: {company[3]}\n"
        f"Контактное лицо: {company[4]}\n"
        f"Email: {company[5]}\n"
        f"Дата создания: {company[6]}"
    )


def create_main_keyboard():
    """Создает основную клавиатуру меню"""
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    buttons = [
        "Добавить компанию",
        "Найти компанию",
        "Отправить данные в API",
        "Показать все компании",
        "Помощь"
    ]
    markup.add(*buttons)
    return markup


def validate_inn(inn: str) -> bool:
    """Валидация ИНН"""
    if not inn.isdigit():
        return False
    if len(inn) not in (10, 12):
        return False
    return True


def validate_email(email: str) -> bool:
    """Валидация email"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None


def validate_phone(phone: str) -> bool:
    """Валидация телефона"""
    phone = phone.replace('+', '').replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
    return phone.isdigit() and len(phone) >= 10


# Инициализация менеджеров
db = DatabaseManager()
api_client = APIClient(API_URL, BOT_ID)


@bot.message_handler(commands=['start'])
def start_message(message):
    """Обработчик команды /start"""
    bot.reply_to(message, 'Привет! Выберите действие:', reply_markup=create_main_keyboard())


@bot.message_handler(func=lambda message: message.text == "Помощь")
def help_message(message):
    """Обработчик кнопки Помощь"""
    help_text = """
    Этот бот помогает работать с данными компаний.
    Доступные команды:
    - Добавить компанию - внести новую компанию в базу
    - Найти компанию - поиск по существующим компаниям
    - Отправить данные в API - отправить данные для обработки и автоматического создания компании
    - Показать все компании - отобразить список всех компаний в базе
    """
    bot.reply_to(message, help_text)


@bot.message_handler(func=lambda message: message.text == "Добавить компанию")
def add_company_start(message):
    """Начало процесса добавления компании"""
    user_states[message.chat.id] = {'state': 'waiting_name', 'data': {}}
    bot.reply_to(message, 'Введите название компании:', reply_markup=telebot.types.ReplyKeyboardRemove())


@bot.message_handler(func=lambda message: message.text == "Показать все компании")
def show_all_companies(message):
    """Отображение всех компаний из базы данных"""
    companies = db.get_all_companies()

    if not companies:
        bot.reply_to(message, "В базе данных нет компаний.")
        return

    response = "Список компаний:\n\n"
    for company in companies:
        response += format_company_info(company) + "\n\n"

    for i in range(0, len(response), 4096):
        bot.send_message(message.chat.id, response[i:i + 4096])


@bot.message_handler(func=lambda message: message.text == "Найти компанию")
def search_company_start(message):
    """Начало процесса поиска компании"""
    search_states[message.chat.id] = {'search_type': None, 'search_value': None}
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    buttons = ["По названию", "По ИНН", "По email"]
    markup.add(*buttons)
    bot.reply_to(message, 'Выберите критерий поиска:', reply_markup=markup)


@bot.message_handler(func=lambda message: message.text in ["По названию", "По ИНН", "По email"])
def set_search_type(message):
    """Установка типа поиска"""
    chat_id = message.chat.id
    if chat_id not in search_states:
        search_states[chat_id] = {}

    if message.text == "По названию":
        search_states[chat_id]['search_type'] = 'названию'
    elif message.text == "По ИНН":
        search_states[chat_id]['search_type'] = 'инн'
    elif message.text == "По email":
        search_states[chat_id]['search_type'] = 'email'

    bot.reply_to(message, f'Введите значение для поиска по {search_states[chat_id]["search_type"]}:',
                 reply_markup=telebot.types.ReplyKeyboardRemove())


@bot.message_handler(func=lambda message: message.chat.id in search_states and
                                          'search_type' in search_states[message.chat.id] and
                                          'search_value' not in search_states[message.chat.id])
def perform_search(message):
    """Выполнение поиска компании"""
    chat_id = message.chat.id
    search_type = search_states[chat_id]['search_type']
    search_value = message.text

    companies = db.search_company(search_type, search_value)

    if not companies:
        bot.reply_to(message, "Компании не найдены.", reply_markup=create_main_keyboard())
    else:
        response = f"Найдено компаний: {len(companies)}\n\n"
        for company in companies:
            response += format_company_info(company) + "\n\n"

        for i in range(0, len(response), 4096):
            bot.send_message(chat_id, response[i:i + 4096], reply_markup=create_main_keyboard())

    del search_states[chat_id]


@bot.message_handler(func=lambda message: message.chat.id in user_states and
                                          user_states[message.chat.id]['state'] == 'waiting_name')
def process_company_name(message):
    """Обработка названия компании"""
    chat_id = message.chat.id
    user_states[chat_id]['data']['name'] = message.text
    user_states[chat_id]['state'] = 'waiting_inn'
    bot.reply_to(message, 'Введите ИНН компании (10 или 12 цифр):')


@bot.message_handler(func=lambda message: message.chat.id in user_states and
                                          user_states[message.chat.id]['state'] == 'waiting_inn')
def process_company_inn(message):
    """Обработка ИНН компании"""
    chat_id = message.chat.id
    inn = message.text

    if not validate_inn(inn):
        bot.reply_to(message, 'Некорректный ИНН. Введите 10 или 12 цифр:')
        return

    user_states[chat_id]['data']['inn'] = inn
    user_states[chat_id]['state'] = 'waiting_phone'
    bot.reply_to(message, 'Введите телефон компании:')


@bot.message_handler(func=lambda message: message.chat.id in user_states and
                                          user_states[message.chat.id]['state'] == 'waiting_phone')
def process_company_phone(message):
    """Обработка телефона компании"""
    chat_id = message.chat.id
    phone = message.text

    if not validate_phone(phone):
        bot.reply_to(message, 'Некорректный телефон. Введите номер в формате +7XXXXXXXXXX или 8XXXXXXXXXX:')
        return

    user_states[chat_id]['data']['phone'] = phone
    user_states[chat_id]['state'] = 'waiting_contact'
    bot.reply_to(message, 'Введите контактное лицо:')


@bot.message_handler(func=lambda message: message.chat.id in user_states and
                                          user_states[message.chat.id]['state'] == 'waiting_contact')
def process_company_contact(message):
    """Обработка контактного лица"""
    chat_id = message.chat.id
    user_states[chat_id]['data']['contact_person'] = message.text
    user_states[chat_id]['state'] = 'waiting_email'
    bot.reply_to(message, 'Введите email компании:')


@bot.message_handler(func=lambda message: message.chat.id in user_states and
                                          user_states[message.chat.id]['state'] == 'waiting_email')
def process_company_email(message):
    """Обработка email компании"""
    chat_id = message.chat.id
    email = message.text

    if not validate_email(email):
        bot.reply_to(message, 'Некорректный email. Введите email в формате example@domain.com:')
        return

    user_states[chat_id]['data']['email'] = email

    try:
        company_id = db.save_company(user_states[chat_id]['data'])
        bot.reply_to(message, '✅ Компания успешно сохранена!', reply_markup=create_main_keyboard())
    except Exception as e:
        bot.reply_to(message, f'⚠️ Ошибка при сохранении компании: {str(e)}', reply_markup=create_main_keyboard())

    del user_states[chat_id]


@bot.message_handler(func=lambda message: message.text == "Отправить данные в API")
def send_to_api_start(message):
    """Начало процесса отправки данных в API"""
    user_states[message.chat.id] = {'state': 'waiting_api_data'}
    bot.reply_to(message, 'Отправьте сообщение или файл с данными компании для обработки API:',
                 reply_markup=telebot.types.ReplyKeyboardRemove())


@bot.message_handler(func=lambda message: message.chat.id in user_states and
                                          user_states[message.chat.id]['state'] == 'waiting_api_data',
                     content_types=['text'])
def process_text_for_api(message):
    """Обработка текстовых сообщений для API"""
    chat_id = message.chat.id

    try:
        response_data = api_client.send_request(
            chat_id,
            message.message_id,
            message.text
        )

        company_data = parse_api_response(response_data)

        if company_data:
            company_id = db.save_company(company_data)
            bot.reply_to(message, "✅ Компания успешно добавлена из API!", reply_markup=create_main_keyboard())
        else:
            bot.reply_to(message, "ℹ️ API не вернул данных компании в требуемом формате",
                         reply_markup=create_main_keyboard())

    except Exception as e:
        logger.error(f"Error processing message: {str(e)}")
        bot.reply_to(message, f"⚠️ Ошибка: {str(e)}", reply_markup=create_main_keyboard())

    if chat_id in user_states:
        del user_states[chat_id]


@bot.message_handler(func=lambda message: message.chat.id in user_states and
                                          user_states[message.chat.id]['state'] == 'waiting_api_data',
                     content_types=['photo', 'document', 'audio', 'video'])
def process_files_for_api(message):
    """Обработка файловых сообщений для API"""
    chat_id = message.chat.id

    try:
        file_info = bot.get_file(message.document.file_id if message.content_type == 'document'
                                 else message.photo[-1].file_id if message.content_type == 'photo'
        else getattr(message, message.content_type).file_id)

        file_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}"

        content = {
            'file': {
                'file_url': file_url,
                'file_type': message.content_type,
                'file_size': getattr(getattr(message, message.content_type), 'file_size', 0),
                'caption': message.caption or ""
            }
        }

        response_data = api_client.send_request(
            chat_id,
            message.message_id,
            content
        )

        company_data = parse_api_response(response_data)

        if company_data:
            company_id = db.save_company(company_data)
            db.save_file(
                company_id=company_id,
                file_url=file_url,
                file_type=message.content_type,
                caption=message.caption
            )
            bot.reply_to(message, "✅ Компания успешно добавлена из API!", reply_markup=create_main_keyboard())
        else:
            bot.reply_to(message, "ℹ️ API не вернул данных компании в требуемом формате",
                         reply_markup=create_main_keyboard())

    except Exception as e:
        logger.error(f"Error processing file: {str(e)}")
        bot.reply_to(message, f"⚠️ Ошибка: {str(e)}", reply_markup=create_main_keyboard())

    if chat_id in user_states:
        del user_states[chat_id]


# Запуск бота
if __name__ == '__main__':
    logger.info("Starting bot...")
    bot.polling(none_stop=True)