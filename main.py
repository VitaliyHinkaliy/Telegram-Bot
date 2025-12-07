import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Получение токена из переменных окружения
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в переменных окружения!")

# Настройка Google Sheets
GOOGLE_CREDENTIALS = os.getenv('GOOGLE_CREDENTIALS_JSON')
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID', '')

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Состояния для FSM
class CalculationStates(StatesGroup):
    # Сценарий 1: рубли + курс → баты + профит
    waiting_rubles_1 = State()
    waiting_rate_1 = State()
    
    # Сценарий 2: баты + курс → рубли + профит
    waiting_baht_2 = State()
    waiting_rate_2 = State()
    
    # Сценарий 3: рубли + профит → баты + курс
    waiting_rubles_3 = State()
    waiting_profit_3 = State()
    
    # Сценарий 4: баты + профит → рубли + курс
    waiting_baht_4 = State()
    waiting_profit_4 = State()
    
    # Состояния для перерасчета
    recalc_waiting_value = State()

# Глобальные переменные для хранения последнего расчета
last_calculation = {}

# Функция для работы с Google Sheets
def get_google_sheet():
    """Подключение к Google Sheets"""
    try:
        if not GOOGLE_CREDENTIALS:
            logger.warning("Google Credentials не найдены, используем тестовые значения")
            return None
            
        # Парсим JSON из переменной окружения
        creds_dict = json.loads(GOOGLE_CREDENTIALS)
        
        scope = ['https://spreadsheets.google.com/feeds',
                 'https://www.googleapis.com/auth/drive']
        
        credentials = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(credentials)
        
        if SPREADSHEET_ID:
            sheet = client.open_by_key(SPREADSHEET_ID).sheet1
            return sheet
        else:
            logger.warning("SPREADSHEET_ID не указан")
            return None
    except Exception as e:
        logger.error(f"Ошибка подключения к Google Sheets: {e}")
        return None

def get_exchange_rates():
    """Получение курсов из Google Sheets"""
    sheet = get_google_sheet()
    
    if sheet:
        try:
            # B2 - курс USDT→THB
            usdt_thb = float(sheet.acell('B2').value.replace(',', '.'))
            # B3 - курс RUB→USDT
            rub_usdt = float(sheet.acell('B3').value.replace(',', '.'))
            
            return {
                'usdt_thb': usdt_thb,
                'rub_usdt': rub_usdt,
                'commission': 0.0025  # 0.25%
            }
        except Exception as e:
            logger.error(f"Ошибка чтения курсов: {e}")
    
    # Тестовые значения, если таблица недоступна
    return {
        'usdt_thb': 31.89,
        'rub_usdt': 79.50,
        'commission': 0.0025
    }

# Функции расчетов
def calculate_rubles_to_baht(rubles: float, client_rate: float):
    """Сценарий 1: рубли + курс → баты + профит"""
    rates = get_exchange_rates()
    
    # Рубли → USDT
    usdt = rubles / rates['rub_usdt']
    
    # USDT → THB (с комиссией)
    thb_real = usdt * rates['usdt_thb'] * (1 - rates['commission'])
    
    # Баты для клиента
    thb_client = rubles / client_rate
    
    # Профит
    profit = thb_real - thb_client
    
    return {
        'rubles': rubles,
        'client_rate': client_rate,
        'thb_client': round(thb_client, 2),
        'profit': round(profit, 2),
        'real_rate': round(rubles / thb_real, 4) if thb_real > 0 else 0
    }

def calculate_baht_to_rubles(baht: float, client_rate: float):
    """Сценарий 2: баты + курс → рубли + профит"""
    rates = get_exchange_rates()
    
    # Рубли для клиента
    rubles_client = baht * client_rate
    
    # THB → USDT → RUB (реальный курс с комиссией)
    usdt = baht / (rates['usdt_thb'] * (1 - rates['commission']))
    rubles_real = usdt * rates['rub_usdt']
    
    # Профит в батах
    profit_rubles = rubles_client - rubles_real
    profit_baht = profit_rubles / client_rate
    
    return {
        'baht': baht,
        'client_rate': client_rate,
        'rubles_client': round(rubles_client, 2),
        'profit': round(profit_baht, 2),
        'rubles_real': round(rubles_real, 2)
    }

def calculate_rubles_profit_to_baht(rubles: float, desired_profit: float):
    """Сценарий 3: рубли + профит → баты + курс"""
    rates = get_exchange_rates()
    
    # Рубли → USDT → THB (реальная сумма)
    usdt = rubles / rates['rub_usdt']
    thb_real = usdt * rates['usdt_thb'] * (1 - rates['commission'])
    
    # Баты для клиента
    thb_client = thb_real - desired_profit
    
    # Курс для клиента
    client_rate = rubles / thb_client if thb_client > 0 else 0
    
    return {
        'rubles': rubles,
        'desired_profit': desired_profit,
        'thb_client': round(thb_client, 2),
        'client_rate': round(client_rate, 4),
        'thb_real': round(thb_real, 2)
    }

def calculate_baht_profit_to_rubles(baht: float, desired_profit: float):
    """Сценарий 4: баты + профит → рубли + курс"""
    rates = get_exchange_rates()
    
    # THB → USDT → RUB (реальная сумма с учетом комиссии)
    usdt = baht / (rates['usdt_thb'] * (1 - rates['commission']))
    rubles_real = usdt * rates['rub_usdt']
    
    # Рубли от клиента (с профитом в батах)
    profit_in_rubles = desired_profit * (rubles_real / baht) if baht > 0 else 0
    rubles_client = rubles_real + profit_in_rubles
    
    # Курс для клиента
    client_rate = rubles_client / baht if baht > 0 else 0
    
    return {
        'baht': baht,
        'desired_profit': desired_profit,
        'rubles_client': round(rubles_client, 2),
        'client_rate': round(client_rate, 4),
        'rubles_real': round(rubles_real, 2)
    }

# Клавиатуры
def get_main_keyboard():
    """Главная клавиатура с выбором сценария"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💰 Рубли + Курс → Баты")],
            [KeyboardButton(text="🇹🇭 Баты + Курс → Рубли")],
            [KeyboardButton(text="📊 Рубли + Профит → Баты")],
            [KeyboardButton(text="💵 Баты + Профит → Рубли")],
            [KeyboardButton(text="📈 Текущие курсы")],
        ],
        resize_keyboard=True
    )
    return keyboard

def get_recalc_keyboard(scenario: int):
    """Клавиатура для перерасчета"""
    buttons = []
    
    if scenario in [1, 3]:
        buttons.append([KeyboardButton(text="🔄 Изменить рубли")])
    if scenario in [2, 4]:
        buttons.append([KeyboardButton(text="🔄 Изменить баты")])
    if scenario in [1, 2]:
        buttons.append([KeyboardButton(text="🔄 Изменить курс")])
    if scenario in [3, 4]:
        buttons.append([KeyboardButton(text="🔄 Изменить профит")])
    
    buttons.append([KeyboardButton(text="◀️ Главное меню")])
    
    keyboard = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
    return keyboard

# Обработчики команд
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    """Команда /start"""
    await state.clear()
    await message.answer(
        "👋 Привет! Я бот для расчета обмена RUB → USDT → THB\n\n"
        "Выберите нужный сценарий расчета:",
        reply_markup=get_main_keyboard()
    )

@dp.message(F.text == "◀️ Главное меню")
async def back_to_menu(message: types.Message, state: FSMContext):
    """Возврат в главное меню"""
    await state.clear()
    await message.answer("Выберите сценарий:", reply_markup=get_main_keyboard())

@dp.message(F.text == "📈 Текущие курсы")
async def show_rates(message: types.Message):
    """Показать текущие курсы"""
    rates = get_exchange_rates()
    
    text = (
        "📊 <b>Текущие курсы:</b>\n\n"
        f"USDT → THB: <b>{rates['usdt_thb']}</b>\n"
        f"RUB → USDT: <b>{rates['rub_usdt']}</b>\n"
        f"Комиссия: <b>{rates['commission'] * 100}%</b>\n\n"
        f"Итоговый курс RUB/THB: <b>{round(rates['rub_usdt'] / rates['usdt_thb'], 4)}</b>"
    )
    
    await message.answer(text, parse_mode="HTML")

# Сценарий 1: Рубли + Курс → Баты + Профит
@dp.message(F.text == "💰 Рубли + Курс → Баты")
async def scenario1_start(message: types.Message, state: FSMContext):
    """Начало сценария 1"""
    await state.set_state(CalculationStates.waiting_rubles_1)
    await message.answer(
        "💰 <b>Сценарий 1: Рубли + Курс → Баты</b>\n\n"
        "Введите сумму в рублях:",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove()
    )

@dp.message(CalculationStates.waiting_rubles_1)
async def scenario1_rubles(message: types.Message, state: FSMContext):
    """Получение суммы рублей"""
    try:
        rubles = float(message.text.replace(',', '.'))
        await state.update_data(rubles=rubles)
        await state.set_state(CalculationStates.waiting_rate_1)
        await message.answer("Введите курс для клиента (например, 2.6):")
    except ValueError:
        await message.answer("❌ Ошибка! Введите число (например: 50000 или 50000.5)")

@dp.message(CalculationStates.waiting_rate_1)
async def scenario1_rate(message: types.Message, state: FSMContext):
    """Получение курса и расчет"""
    try:
        rate = float(message.text.replace(',', '.'))
        data = await state.get_data()
        rubles = data['rubles']
        
        result = calculate_rubles_to_baht(rubles, rate)
        
        # Сохраняем результат
        global last_calculation
        last_calculation[message.from_user.id] = {
            'scenario': 1,
            'result': result
        }
        
        text = (
            "✅ <b>Результат расчета:</b>\n\n"
            f"💵 Рубли: <b>{result['rubles']:,.2f}</b>\n"
            f"📊 Курс для клиента: <b>{result['client_rate']}</b>\n"
            f"🇹🇭 Баты для клиента: <b>{result['thb_client']:,.2f}</b>\n"
            f"💰 Ваш профит: <b>{result['profit']:,.2f}</b> THB\n\n"
            f"<i>Реальный курс: {result['real_rate']}</i>"
        )
        
        await state.clear()
        await message.answer(text, parse_mode="HTML", reply_markup=get_recalc_keyboard(1))
    except ValueError:
        await message.answer("❌ Ошибка! Введите число (например: 2.6)")

# Сценарий 2: Баты + Курс → Рубли + Профит
@dp.message(F.text == "🇹🇭 Баты + Курс → Рубли")
async def scenario2_start(message: types.Message, state: FSMContext):
    """Начало сценария 2"""
    await state.set_state(CalculationStates.waiting_baht_2)
    await message.answer(
        "🇹🇭 <b>Сценарий 2: Баты + Курс → Рубли</b>\n\n"
        "Введите количество батов:",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove()
    )

@dp.message(CalculationStates.waiting_baht_2)
async def scenario2_baht(message: types.Message, state: FSMContext):
    """Получение батов"""
    try:
        baht = float(message.text.replace(',', '.'))
        await state.update_data(baht=baht)
        await state.set_state(CalculationStates.waiting_rate_2)
        await message.answer("Введите курс для клиента:")
    except ValueError:
        await message.answer("❌ Ошибка! Введите число")

@dp.message(CalculationStates.waiting_rate_2)
async def scenario2_rate(message: types.Message, state: FSMContext):
    """Получение курса и расчет"""
    try:
        rate = float(message.text.replace(',', '.'))
        data = await state.get_data()
        baht = data['baht']
        
        result = calculate_baht_to_rubles(baht, rate)
        
        global last_calculation
        last_calculation[message.from_user.id] = {
            'scenario': 2,
            'result': result
        }
        
        text = (
            "✅ <b>Результат расчета:</b>\n\n"
            f"🇹🇭 Баты: <b>{result['baht']:,.2f}</b>\n"
            f"📊 Курс для клиента: <b>{result['client_rate']}</b>\n"
            f"💵 Рублей от клиента: <b>{result['rubles_client']:,.2f}</b>\n"
            f"💰 Ваш профит: <b>{result['profit']:,.2f}</b> THB\n\n"
            f"<i>Реальная стоимость: {result['rubles_real']:,.2f} RUB</i>"
        )
        
        await state.clear()
        await message.answer(text, parse_mode="HTML", reply_markup=get_recalc_keyboard(2))
    except ValueError:
        await message.answer("❌ Ошибка! Введите число")

# Сценарий 3: Рубли + Профит → Баты + Курс
@dp.message(F.text == "📊 Рубли + Профит → Баты")
async def scenario3_start(message: types.Message, state: FSMContext):
    """Начало сценария 3"""
    await state.set_state(CalculationStates.waiting_rubles_3)
    await message.answer(
        "📊 <b>Сценарий 3: Рубли + Профит → Баты</b>\n\n"
        "Введите сумму в рублях:",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove()
    )

@dp.message(CalculationStates.waiting_rubles_3)
async def scenario3_rubles(message: types.Message, state: FSMContext):
    """Получение рублей"""
    try:
        rubles = float(message.text.replace(',', '.'))
        await state.update_data(rubles=rubles)
        await state.set_state(CalculationStates.waiting_profit_3)
        await message.answer("Введите желаемый профит в батах:")
    except ValueError:
        await message.answer("❌ Ошибка! Введите число")

@dp.message(CalculationStates.waiting_profit_3)
async def scenario3_profit(message: types.Message, state: FSMContext):
    """Получение профита и расчет"""
    try:
        profit = float(message.text.replace(',', '.'))
        data = await state.get_data()
        rubles = data['rubles']
        
        result = calculate_rubles_profit_to_baht(rubles, profit)
        
        global last_calculation
        last_calculation[message.from_user.id] = {
            'scenario': 3,
            'result': result
        }
        
        text = (
            "✅ <b>Результат расчета:</b>\n\n"
            f"💵 Рубли: <b>{result['rubles']:,.2f}</b>\n"
            f"💰 Желаемый профит: <b>{result['desired_profit']:,.2f}</b> THB\n"
            f"🇹🇭 Баты для клиента: <b>{result['thb_client']:,.2f}</b>\n"
            f"📊 Курс для клиента: <b>{result['client_rate']}</b>\n\n"
            f"<i>Реальная сумма: {result['thb_real']:,.2f} THB</i>"
        )
        
        await state.clear()
        await message.answer(text, parse_mode="HTML", reply_markup=get_recalc_keyboard(3))
    except ValueError:
        await message.answer("❌ Ошибка! Введите число")

# Сценарий 4: Баты + Профит → Рубли + Курс
@dp.message(F.text == "💵 Баты + Профит → Рубли")
async def scenario4_start(message: types.Message, state: FSMContext):
    """Начало сценария 4"""
    await state.set_state(CalculationStates.waiting_baht_4)
    await message.answer(
        "💵 <b>Сценарий 4: Баты + Профит → Рубли</b>\n\n"
        "Введите количество батов:",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove()
    )

@dp.message(CalculationStates.waiting_baht_4)
async def scenario4_baht(message: types.Message, state: FSMContext):
    """Получение батов"""
    try:
        baht = float(message.text.replace(',', '.'))
        await state.update_data(baht=baht)
        await state.set_state(CalculationStates.waiting_profit_4)
        await message.answer("Введите желаемый профит в батах:")
    except ValueError:
        await message.answer("❌ Ошибка! Введите число")

@dp.message(CalculationStates.waiting_profit_4)
async def scenario4_profit(message: types.Message, state: FSMContext):
    """Получение профита и расчет"""
    try:
        profit = float(message.text.replace(',', '.'))
        data = await state.get_data()
        baht = data['baht']
        
        result = calculate_baht_profit_to_rubles(baht, profit)
        
        global last_calculation
        last_calculation[message.from_user.id] = {
            'scenario': 4,
            'result': result
        }
        
        text = (
            "✅ <b>Результат расчета:</b>\n\n"
            f"🇹🇭 Баты: <b>{result['baht']:,.2f}</b>\n"
            f"💰 Желаемый профит: <b>{result['desired_profit']:,.2f}</b> THB\n"
            f"💵 Рублей от клиента: <b>{result['rubles_client']:,.2f}</b>\n"
            f"📊 Курс для клиента: <b>{result['client_rate']}</b>\n\n"
            f"<i>Реальная стоимость: {result['rubles_real']:,.2f} RUB</i>"
        )
        
        await state.clear()
        await message.answer(text, parse_mode="HTML", reply_markup=get_recalc_keyboard(4))
    except ValueError:
        await message.answer("❌ Ошибка! Введите число")

# Обработчики перерасчета
@dp.message(F.text.startswith("🔄 Изменить"))
async def handle_recalculation(message: types.Message, state: FSMContext):
    """Обработка кнопок перерасчета"""
    user_id = message.from_user.id
    
    if user_id not in last_calculation:
        await message.answer("❌ Нет сохраненных расчетов. Начните новый расчет.")
        return
    
    calc_data = last_calculation[user_id]
    scenario = calc_data['scenario']
    
    # Определяем что именно меняем
    if "рубли" in message.text.lower():
        await state.update_data(recalc_type='rubles', scenario=scenario)
        await state.set_state(CalculationStates.recalc_waiting_value)
        await message.answer("Введите новую сумму в рублях:", reply_markup=ReplyKeyboardRemove())
    
    elif "баты" in message.text.lower():
        await state.update_data(recalc_type='baht', scenario=scenario)
        await state.set_state(CalculationStates.recalc_waiting_value)
        await message.answer("Введите новое количество батов:", reply_markup=ReplyKeyboardRemove())
    
    elif "курс" in message.text.lower():
        await state.update_data(recalc_type='rate', scenario=scenario)
        await state.set_state(CalculationStates.recalc_waiting_value)
        await message.answer("Введите новый курс:", reply_markup=ReplyKeyboardRemove())
    
    elif "профит" in message.text.lower():
        await state.update_data(recalc_type='profit', scenario=scenario)
        await state.set_state(CalculationStates.recalc_waiting_value)
        await message.answer("Введите новый профит:", reply_markup=ReplyKeyboardRemove())

@dp.message(CalculationStates.recalc_waiting_value)
async def process_recalculation(message: types.Message, state: FSMContext):
    """Обработка нового значения и пересчет"""
    try:
        new_value = float(message.text.replace(',', '.'))
        data = await state.get_data()
        recalc_type = data['recalc_type']
        scenario = data['scenario']
        
        user_id = message.from_user.id
        old_result = last_calculation[user_id]['result']
        
        # Выполняем перерасчет в зависимости от сценария и типа изменения
        if scenario == 1:
            if recalc_type == 'rubles':
                result = calculate_rubles_to_baht(new_value, old_result['client_rate'])
            else:  # rate
                result = calculate_rubles_to_baht(old_result['rubles'], new_value)
            
            text = (
                "✅ <b>Пересчет:</b>\n\n"
                f"💵 Рубли: <b>{result['rubles']:,.2f}</b>\n"
                f"📊 Курс для клиента: <b>{result['client_rate']}</b>\n"
                f"🇹🇭 Баты для клиента: <b>{result['thb_client']:,.2f}</b>\n"
                f"💰 Ваш профит: <b>{result['profit']:,.2f}</b> THB"
            )
        
        elif scenario == 2:
            if recalc_type == 'baht':
                result = calculate_baht_to_rubles(new_value, old_result['client_rate'])
            else:  # rate
                result = calculate_baht_to_rubles(old_result['baht'], new_value)
            
            text = (
                "✅ <b>Пересчет:</b>\n\n"
                f"🇹🇭 Баты: <b>{result['baht']:,.2f}</b>\n"
                f"📊 Курс для клиента: <b>{result['client_rate']}</b>\n"
                f"💵 Рублей от клиента: <b>{result['rubles_client']:,.2f}</b>\n"
                f"💰 Ваш профит: <b>{result['profit']:,.2f}</b> THB"
            )
        
        elif scenario == 3:
            if recalc_type == 'rubles':
                result = calculate_rubles_profit_to_baht(new_value, old_result['desired_profit'])
            else:  # profit
                result = calculate_rubles_profit_to_baht(old_result['rubles'], new_value)
            
            text = (
                "✅ <b>Пересчет:</b>\n\n"
                f"💵 Рубли: <b>{result['rubles']:,.2f}</b>\n"
                f"💰 Желаемый профит: <b>{result['desired_profit']:,.2f}</b> THB\n"
                f"🇹🇭 Баты для клиента: <b>{result['thb_client']:,.2f}</b>\n"
                f"📊 Курс для клиента: <b>{result['client_rate']}</b>"
            )
        
        elif scenario == 4:
            if recalc_type == 'baht':
                result = calculate_baht_profit_to_rubles(new_value, old_result['desired_profit'])
            else:  # profit
                result = calculate_baht_profit_to_rubles(old_result['baht'], new_value)
            
            text = (
                "✅ <b>Пересчет:</b>\n\n"
                f"🇹🇭 Баты: <b>{result['baht']:,.2f}</b>\n"
                f"💰 Желаемый профит: <b>{result['desired_profit']:,.2f}</b> THB\n"
                f"💵 Рублей от клиента: <b>{result['rubles_client']:,.2f}</b>\n"
                f"📊 Курс для клиента: <b>{result['client_rate']}</b>"
            )
        
        # Сохраняем новый результат
        last_calculation[user_id]['result'] = result
        
        await state.clear()
        await message.answer(text, parse_mode="HTML", reply_markup=get_recalc_keyboard(scenario))
    
    except ValueError:
        await message.answer("❌ Ошибка! Введите число")
    except Exception as e:
        logger.error(f"Ошибка перерасчета: {e}")
        await message.answer("❌ Произошла ошибка при пересчете")

# Запуск бота
async def main():
    logger.info("Запуск бота...")
    try:
        # Удаляем вебхуки если есть
        await bot.delete_webhook(drop_pending_updates=True)
        # Запускаем polling
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Ошибка запуска: {e}")
    finally:
        await bot.session.close()

if __name__ == '__main__':
    asyncio.run(main())
