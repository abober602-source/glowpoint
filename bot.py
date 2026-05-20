import asyncio
import xml.etree.ElementTree as ET
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

BOT_TOKEN = "8883166599:AAGOqYBCx7qmEDdxYVcZe_yGku9dfnU66Qk"
MANAGER = "@GlowPoint_order"

async def get_cny_rate():
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get("https://www.cbr.ru/scripts/XML_daily.asp")
            root = ET.fromstring(r.content)
            for v in root.findall("Valute"):
                if v.find("CharCode").text == "CNY":
                    nominal = int(v.find("Nominal").text)
                    value = float(v.find("Value").text.replace(",", "."))
                    return value / nominal
    except Exception:
        return None

HEADERS = {
    "User-Agent": "DeWu/8.50.0 (iPhone; iOS 16.0)",
    "Accept": "application/json",
    "Accept-Language": "zh-CN",
}

async def search_products(keyword):
    encoded = keyword.replace(" ", "+")
    urls = [
        f"https://app.dewu.com/api/v1/h5/sneaker/home/search/v2?keyword={encoded}&page=1&limit=8",
        f"https://app.dewu.com/api/v1/h5/search/product/list?keyword={encoded}&page=1&pageSize=8",
    ]
    for url in urls:
        try:
            async with httpx.AsyncClient(timeout=15, headers=HEADERS, follow_redirects=True) as client:
                r = await client.get(url)
                if r.status_code != 200:
                    continue
                data = r.json()
                items = (
                    data.get("data", {}).get("list") or
                    data.get("data", {}).get("productList") or
                    data.get("data", {}).get("items") or
                    (data.get("data") if isinstance(data.get("data"), list) else []) or []
                )
                if not isinstance(items, list) or not items:
                    continue
                results = []
                for item in items[:6]:
                    spu_id = str(item.get("spuId") or item.get("productId") or item.get("id") or "")
                    title = item.get("title") or item.get("name") or item.get("productName") or ""
                    price_raw = item.get("lowestPrice") or item.get("price") or item.get("salePrice") or 0
                    yuan = price_raw / 100 if price_raw > 9999 else float(price_raw)
                    if spu_id and title:
                        results.append({"id": spu_id, "title": title[:60], "price_yuan": yuan})
                if results:
                    return results
        except Exception as e:
            print(f"Search error: {e}")
            continue
    return []

async def get_product_sizes(spu_id):
    endpoints = [
        f"https://app.dewu.com/api/v1/h5/sneaker/product/detail/productId/{spu_id}",
        f"https://h5api.dewu.com/api/v1/h5/product/detail?spuId={spu_id}",
    ]
    for url in endpoints:
        try:
            async with httpx.AsyncClient(timeout=15, headers=HEADERS, follow_redirects=True) as client:
                r = await client.get(url)
                if r.status_code != 200:
                    continue
                product = r.json().get("data") or {}
                if not product:
                    continue
                title = product.get("title") or product.get("name") or "Товар"
                sizes = []
                for key in ("propertyValueItems", "sizeList", "skuList", "specs"):
                    for item in product.get(key, []):
                        name = item.get("propertyValueName") or item.get("sizeName") or item.get("size") or item.get("name") or ""
                        price = item.get("price") or item.get("lowestPrice") or item.get("salePrice") or 0
                        if price and name:
                            yuan = price / 100 if price > 9999 else float(price)
                            sizes.append({"name": str(name), "price_yuan": yuan})
                    if sizes:
                        break
                if not sizes:
                    price = product.get("lowestPrice") or product.get("price") or 0
                    if price:
                        yuan = price / 100 if price > 9999 else float(price)
                        sizes.append({"name": "---", "price_yuan": yuan})
                if sizes:
                    return {"title": title, "sizes": sizes}
        except Exception as e:
            print(f"Sizes error: {e}")
            continue
    return None

def calculate(yuan, cbr_rate):
    return round(yuan * (cbr_rate + 0.5) + 2000)

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Напиши название товара на английском, я найду его на Пойзоне и посчитаю цену.\n\n"
        "Примеры:\n"
        "Nike Air Force 1\n"
        "Adidas Samba OG\n"
        "New Balance 550\n"
        "Byredo Blanche"
    )

async def handle_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    if len(query) < 3:
        await update.message.reply_text("Введи название подлиннее.")
        return
    msg = await update.message.reply_text(f'Ищу "{query}"...')
    results = await search_products(query)
    if not results:
        await msg.edit_text(
            f'Ничего не нашел по запросу "{query}".\n\n'
            f'Попробуй написать точнее или обратись к менеджеру: {MANAGER}'
        )
        return
    keyboard = []
    for item in results:
        preview = f"{item['price_yuan']:.0f} ю" if item["price_yuan"] else "цена по запросу"
        label = f"{item['title'][:42]} ({preview})"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"p:{item['id']}")])
    keyboard.append([InlineKeyboardButton("Отмена", callback_data="cancel")])
    await msg.edit_text(
        f'Результаты по "{query}". Выбери товар:',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_product_select(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "cancel":
        await q.edit_message_text("Отменено. Напиши название следующего товара.")
        return
    spu_id = q.data[2:]
    await q.edit_message_text("Загружаю цены по размерам...")
    product, cny_rate = await asyncio.gather(get_product_sizes(spu_id), get_cny_rate())
    if not cny_rate:
        await q.edit_message_text(f"Не удалось загрузить курс ЦБ. Попробуй позже.\n{MANAGER}")
        return
    if not product:
        await q.edit_message_text(f"Не удалось загрузить цены.\n\nНапиши менеджеру: {MANAGER}")
        return
    title = product["title"]
    sizes = product["sizes"]
    lines = [
        f"*{title}*\n",
        f"Курс ЦБ: {cny_rate:.2f} руб.",
        f"Расчетный курс: {cny_rate + 0.5:.2f} руб.\n",
    ]
    if len(sizes) == 1 and sizes[0]["name"] == "---":
        price_rub = calculate(sizes[0]["price_yuan"], cny_rate)
        lines += [
            f"Цена на Пойзоне: {sizes[0]['price_yuan']:.0f} ю",
            f"\n*Итого для вас: {price_rub:,} руб.*",
        ]
    else:
        lines.append("Цены по размерам:\n")
        for s in sizes:
            price_rub = calculate(s["price_yuan"], cny_rate)
            lines.append(f"  {s['name']} - *{price_rub:,} руб.* ({s['price_yuan']:.0f} ю)")
    lines.append(f"\nДля заказа: {MANAGER}")
    lines.append("\nНапиши другое название для нового поиска")
    await q.edit_message_text("\n".join(lines), parse_mode="Markdown")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(handle_product_select, pattern=r"^p:"))
    app.add_handler(CallbackQueryHandler(
        lambda u, c: u.callback_query.edit_message_text("Отменено. Напиши название следующего товара."),
        pattern=r"^cancel$"
    ))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search))
    print("Bot started")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

