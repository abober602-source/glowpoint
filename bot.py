#!/usr/bin/env python3
"""
GlowPoint Price Bot v2
Formula: price_yuan x (cbr_rate + 0.5) + 2000
"""

import asyncio
import xml.etree.ElementTree as ET
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ══════════════════════════════════════════════
#  НАСТРОЙКИ
# ══════════════════════════════════════════════

BOT_TOKEN = "8883166599:AAGOqYBCx7qmEDdxYVcZe_yGku9dfnU66Qk"
MANAGER   = "@GlowPoint_order"

# ══════════════════════════════════════════════
#  КУРС ЦБ РФ
# ══════════════════════════════════════════════

async def get_cny_rate() -> float | None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get("https://www.cbr.ru/scripts/XML_daily.asp")
            root = ET.fromstring(r.content)
            for v in root.findall("Valute"):
                if v.find("CharCode").text == "CNY":
                    nominal = int(v.find("Nominal").text)
                    value   = float(v.find("Value").text.replace(",", "."))
                    return value / nominal
    except Exception:
        return None

# ══════════════════════════════════════════════
#  ПОИСК ТОВАРОВ
# ══════════════════════════════════════════════

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
    ),
    "Accept":          "application/json",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer":         "https://www.poizon.com/",
    "Origin":          "https://www.poizon.com",
}


async def search_products(keyword: str) -> list[dict]:
    """
    Ищет товары по названию. Пробует несколько эндпоинтов Пойзона.
    Возвращает: [{"id": str, "title": str, "price_yuan": float}, ...]
    """
    encoded = keyword.replace(" ", "+")

    endpoints = [
        f"https://app.dewu.com/api/v1/h5/sneaker/home/search/v2?keyword={encoded}&page=1&limit=8",
        f"https://app.dewu.com/api/v1/h5/search/product/list?keyword={encoded}&page=1&pageSize=8",
        f"https://h5api.dewu.com/api/v1/h5/sneaker/home/search?keyword={encoded}&page=1&size=8",
    ]

    for url in endpoints:
        try:
            async with httpx.AsyncClient(
                timeout=15, headers=HEADERS, follow_redirects=True
            ) as client:
                r = await client.get(url)
                if r.status_code != 200:
                    continue

                data = r.json()

                # Разные API кладут список в разные ключи
                items = (
                    data.get("data", {}).get("list")
                    or data.get("data", {}).get("productList")
                    or data.get("data", {}).get("items")
                    or (data.get("data") if isinstance(data.get("data"), list) else None)
                    or []
                )

                if not isinstance(items, list) or not items:
                    continue

                results = []
                for item in items[:6]:
                    spu_id = str(
                        item.get("spuId") or item.get("productId") or item.get("id") or ""
                    )
                    title = (
                        item.get("title")
                        or item.get("name")
                        or item.get("productName")
                        or ""
                    )
                    price_raw = (
                        item.get("lowestPrice")
                        or item.get("price")
                        or item.get("salePrice")
                        or 0
                    )
                    yuan = price_raw / 100 if price_raw > 9999 else float(price_raw)

                    if spu_id and title:
                        results.append({
                            "id":         spu_id,
                            "title":      title[:60],
                            "price_yuan": yuan,
                        })

                if results:
                    return results

        except Exception:
            continue

    return []


async def get_product_sizes(spu_id: str) -> dict | None:
    """
    Загружает размеры и цены конкретного товара по его ID.
    Возвращает: {"title": str, "sizes": [{"name": str, "price_yuan": float}]}
    """
    endpoints = [
        f"https://app.dewu.com/api/v1/h5/sneaker/product/detail/productId/{spu_id}",
        f"https://h5api.dewu.com/api/v1/h5/product/detail?spuId={spu_id}",
    ]

    for url in endpoints:
        try:
            async with httpx.AsyncClient(
                timeout=15, headers=HEADERS, follow_redirects=True
            ) as client:
                r = await client.get(url)
                if r.status_code != 200:
                    continue

                product = r.json().get("data") or {}
                if not product:
                    continue

                title = (
                    product.get("title")
                    or product.get("name")
                    or product.get("productName")
                    or "Товар"
                )

                sizes = []
                for key in ("propertyValueItems", "sizeList", "skuList", "specs"):
                    for item in product.get(key, []):
                        name = (
                            item.get("propertyValueName")
                            or item.get("sizeName")
                            or item.get("size")
                            or item.get("name")
                            or ""
                        )
                        price = (
                            item.get("price")
                            or item.get("lowestPrice")
                            or item.get("salePrice")
                            or 0
                        )
                        if price and name:
                            yuan = price / 100 if price > 9999 else float(price)
                            sizes.append({"name": str(name), "price_yuan": yuan})
                    if sizes:
                        break

                # Нет размеров — берём общую цену товара
                if not sizes:
                    price = product.get("lowestPrice") or product.get("price") or 0
                    if price:
                        yuan = price / 100 if price > 9999 else float(price)
                        sizes.append({"name": "—", "price_yuan": yuan})

                if sizes:
                    return {"title": title, "sizes": sizes}

        except Exception:
            continue

    return None

# ══════════════════════════════════════════════
#  РАСЧЁТ ЦЕНЫ
# ══════════════════════════════════════════════

def calculate(yuan: float, cbr_rate: float) -> int:
    """цена_юань × (курс_ЦБ + 0.5) + 2000"""
    return round(yuan * (cbr_rate + 0.5) + 2000)

# ══════════════════════════════════════════════
#  ХЕНДЛЕРЫ
# ══════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Это GlowPoint! Напиши название товара на английском, "
        "и я посчитаю цену в рублях.\n\n"
        "Примеры:\n"
        "• Nike Air Force 1\n"
        "• Adidas Samba OG\n"
        "• New Balance 550\n"
        "• Byredo Blanche\n"
        "• Coach Satchel 18"
    )


async def handle_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()

    if len(query) < 3:
        await update.message.reply_text("Введи название подлиннее — хотя бы 3 символа.")
        return

    msg = await update.message.reply_text(f'🔍 Ищу "{query}"...')

    results = await search_products(query)

    if not results:
        await msg.edit_text(
            f'❌ Ничего не нашёл по запросу "{query}".\n\n'
            "Попробуй:\n"
            "• Написать точнее: не «Nike кроссовки», а «Nike Air Max 90»\n"
            "• Использовать оригинальное название на английском\n\n"
            f"Или напиши менеджеру: {MANAGER}"
        )
        return

    # Кнопки с результатами поиска
    keyboard = []
    for item in results:
        preview = f"{item['price_yuan']:.0f} ¥" if item["price_yuan"] else "цена по запросу"
        label   = f"{item['title'][:42]}  ({preview})"
        keyboard.append([
            InlineKeyboardButton(label, callback_data=f"p:{item['id']}")
        ])
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])

    await msg.edit_text(
        f'Результаты по запросу "{query}".\nВыбери нужный товар 👇',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_product_select(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "cancel":
        await q.edit_message_text("Поиск отменён. Напиши название следующего товара.")
        return

    spu_id = q.data[2:]  # убираем префикс "p:"
    await q.edit_message_text("⏳ Загружаю цены по размерам...")

    product, cny_rate = await asyncio.gather(
        get_product_sizes(spu_id),
        get_cny_rate()
    )

    if not cny_rate:
        await q.edit_message_text(
            "❌ Не удалось загрузить курс ЦБ. Попробуй через минуту.\n"
            f"По вопросам: {MANAGER}"
        )
        return

    if not product:
        await q.edit_message_text(
            "❌ Не удалось загрузить цены по этому товару.\n"
            "Возможно, он временно недоступен.\n\n"
            f"Напиши менеджеру: {MANAGER}"
        )
        return

    title = product["title"]
    sizes = product["sizes"]

    lines = [
        f"🛍 *{title}*\n",
        f"💵 Курс ЦБ: {cny_rate:.2f} ₽",
        f"⚙️ Расчётный курс: {cny_rate + 0.5:.2f} ₽\n",
    ]

    if len(sizes) == 1 and sizes[0]["name"] == "—":
        # Нет разбивки по размерам — одна цена
        price_rub = calculate(sizes[0]["price_yuan"], cny_rate)
        lines += [
            f"💴 Цена на Пойзоне: {sizes[0]['price_yuan']:.0f} ¥",
            f"\n💰 *Итого для вас: {price_rub:,} ₽*",
        ]
    else:
        lines.append("📏 *Цены по размерам:*\n")
        for s in sizes:
            price_rub = calculate(s["price_yuan"], cny_rate)
            lines.append(
                f"  `{s['name']:>6}` — *{price_rub:,} ₽*  ({s['price_yuan']:.0f} ¥)"
            )

    lines.append(f"\n📦 Для заказа: {MANAGER}")
    lines.append("\n_Напиши другое название для нового поиска_")

    await q.edit_message_text("\n".join(lines), parse_mode="Markdown")

# ══════════════════════════════════════════════
#  ЗАПУСК
# ══════════════════════════════════════════════

def main():
    if BOT_TOKEN == "ВСТАВЬТЕ_ТОКЕН_ЗДЕСЬ":
        print("❌ Вставьте токен бота в переменную BOT_TOKEN")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(handle_product_select, pattern=r"^p:"))
    app.add_handler(CallbackQueryHandler(
        lambda u, c: u.callback_query.edit_message_text(
            "Поиск отменён. Напиши название следующего товара."
        ),
        pattern=r"^cancel$"
    ))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search))

    print("✅ Бот запущен")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
