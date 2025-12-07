import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.utils.keyboard import ReplyKeyboardMarkup, KeyboardButton

from config import BOT_TOKEN, SHEETS_ID
from gsheets import get_sheet

dp = Dispatcher()

# Главное меню
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💱 Быстрый расчёт")],
        [KeyboardButton(text="🎯 Подбор курса по батам+профиту")],
        [KeyboardButton(text="🎯 Подбор курса по рублям+профиту")],
    ],
    resize_keyboard=True
)

@dp.message(commands=["start"])
async def start_cmd(message: types.Message):
    await message.answer(
        "Бот работает! Выберите действие:",
        reply_markup=main_kb
    )

@dp.message(lambda m: m.text == "💱 Быстрый расчёт")
async def fast_calc(message: types.Message):
    await message.answer("Введи сумму рублей:")

    @dp.message()
    async def get_rub(msg: types.Message):
        rubles = float(msg.text)

        sheet = get_sheet(SHEETS_ID)
        ws = sheet.worksheet("расчет")   # твой лист

        # Читаем курс USDT→THB из B2
        usdt_thb = float(ws.acell("B2").value)

        # Читаем курс RUB→USDT из B3
        rub_usdt = float(ws.acell("B3").value)

        # Пересчёт — можно менять на твою формулу
        usdt = rubles / rub_usdt
        thb = usdt * usdt_thb

        await msg.answer(
            f"Рубли: {rubles}\n"
            f"USDT: {usdt:.2f}\n"
            f"Баты: {thb:.2f}"
        )

        # Снова делаем главное меню
        await msg.answer("Выбери действие:", reply_markup=main_kb)
