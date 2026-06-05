import logging
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, filters, ContextTypes
)
from database import Database

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "123456789").split(",")))
DATABASE_URL = os.getenv("DATABASE_URL")

db = Database(DATABASE_URL)


def fmt(cents: int) -> str:
    """Sentlarni dollar formatida ko'rsatish"""
    return "{:.2f}".format(cents / 100)

# States
(
    MAIN_MENU, CATALOG, CART,
    ADMIN_MENU, ADMIN_ADD_NAME, ADMIN_ADD_PRICE, ADMIN_ADD_PHOTO, ADMIN_ADD_CONFIRM,
    ADMIN_REDUCE_DEBT_AMOUNT,
    ADMIN_ADD_DEBT_AMOUNT,
    ADMIN_BROADCAST,
    CLIENT_PHONE
) = range(12)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def main_menu_keyboard(is_admin_user: bool = False):
    buttons = [
        [KeyboardButton("🛍 Katalog"), KeyboardButton("🛒 Savatcha")],
        [KeyboardButton("💰 Mening qarzim"), KeyboardButton("📋 Buyurtmalarim")],
    ]
    if is_admin_user:
        buttons.append([KeyboardButton("⚙️ Admin panel")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


def admin_menu_keyboard():
    buttons = [
        [KeyboardButton("➕ Mahsulot qo'shish"), KeyboardButton("📦 Mahsulotlar")],
        [KeyboardButton("👥 Mijozlar"), KeyboardButton("💳 Qarzlar")],
        [KeyboardButton("📋 Buyurtmalar"), KeyboardButton("📢 Xabar yuborish")],
        [KeyboardButton("🔙 Asosiy menyu")],
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


# --- START ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    existing = await db.get_user(user.id)

    if not existing:
        await db.create_user(user.id, user.full_name, user.username)
        await update.message.reply_text(
            f"Assalomu alaykum, {user.first_name}! 👋\n\nDo'konimizga xush kelibsiz!\nIltimos, telefon raqamingizni yuboring.",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("📱 Telefon raqamni yuborish", request_contact=True)]],
                resize_keyboard=True
            )
        )
        return CLIENT_PHONE
    else:
        await update.message.reply_text(
            f"Xush kelibsiz, {user.first_name}! 🛍",
            reply_markup=main_menu_keyboard(is_admin(user.id))
        )
        return MAIN_MENU


async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    if contact:
        await db.update_user_phone(update.effective_user.id, contact.phone_number)
    await update.message.reply_text(
        "Rahmat! Ro'yxatdan o'tdingiz ✅",
        reply_markup=main_menu_keyboard(is_admin(update.effective_user.id))
    )
    return MAIN_MENU


# --- MAIN MENU ---

async def handle_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id

    if text == "🛍 Katalog":
        return await show_catalog(update, context)
    elif text == "🛒 Savatcha":
        return await show_cart(update, context)
    elif text == "💰 Mening qarzim":
        return await show_my_debt(update, context)
    elif text == "📋 Buyurtmalarim":
        return await show_my_orders(update, context)
    elif text == "⚙️ Admin panel" and is_admin(user_id):
        await update.message.reply_text("Admin panel:", reply_markup=admin_menu_keyboard())
        return ADMIN_MENU
    return MAIN_MENU


async def get_product_keyboard(user_id: int, product_id: int) -> InlineKeyboardMarkup:
    cart = await db.get_cart(user_id)
    qty = next((item['quantity'] for item in cart if item['product_id'] == product_id), 0)
    if qty == 0:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 Savatga qo'shish", callback_data=f"add_cart_{product_id}")]
        ])
    else:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("➖", callback_data=f"dec_cart_{product_id}"),
                InlineKeyboardButton(str(qty), callback_data="noop"),
                InlineKeyboardButton("➕", callback_data=f"add_cart_{product_id}"),
            ]
        ])


async def show_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    products = await db.get_all_products()
    if not products:
        await update.message.reply_text("Hozircha mahsulotlar mavjud emas.")
        return MAIN_MENU

    await update.message.reply_text("📦 Mahsulotlar katalogi:")
    for product in products:
        keyboard = await get_product_keyboard(user_id, product['id'])
        caption = f"*{product['name']}*\n💵 Narx: {fmt(product['price'])} $"
        if product['photo_id']:
            try:
                await update.message.reply_photo(
                    photo=product['photo_id'],
                    caption=caption,
                    parse_mode='Markdown',
                    reply_markup=keyboard
                )
            except Exception:
                await update.message.reply_text(caption, parse_mode='Markdown', reply_markup=keyboard)
        else:
            await update.message.reply_text(caption, parse_mode='Markdown', reply_markup=keyboard)
    return CATALOG


async def show_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cart_items = await db.get_cart(user_id)

    if not cart_items:
        await update.message.reply_text("🛒 Savatchingiz bo'sh.")
        return MAIN_MENU

    total = sum(item['price'] * item['quantity'] for item in cart_items)
    text = "🛒 *Savatchangiz:*\n\n"
    for item in cart_items:
        text += f"• {item['name']} x{item['quantity']} = {fmt(item['price'] * item['quantity'])} $\n"
    text += f"\n💰 *Jami: {fmt(total)} $*"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Buyurtma berish", callback_data="checkout")],
        [InlineKeyboardButton("🗑 Savatchani tozalash", callback_data="clear_cart")]
    ])
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=keyboard)
    return CART


async def show_my_debt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = await db.get_user(user_id)
    debt = user['total_debt'] if user else 0

    text = "💰 *Sizning qarzingiz:*\n\n"
    if debt > 0:
        text += f"Jami qarz: *{fmt(debt)} $*"
    else:
        text += "✅ Qarzingiz yo'q!"

    await update.message.reply_text(text, parse_mode='Markdown')
    return MAIN_MENU


async def show_my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    orders = await db.get_user_orders(user_id)

    if not orders:
        await update.message.reply_text("📋 Buyurtmalaringiz yo'q.")
        return MAIN_MENU

    text = "📋 *Buyurtmalaringiz:*\n\n"
    for order in orders[:10]:
        text += f"#{order['id']} — {fmt(order['total_amount'])} $"
        if order['debt_amount'] > 0:
            text += f" | 💰 Qarz: {fmt(order['debt_amount'])} $"
        text += f"\n📅 {order['created_at'].strftime('%d.%m.%Y %H:%M')}\n\n"

    await update.message.reply_text(text, parse_mode='Markdown')
    return MAIN_MENU


# --- CALLBACKS ---

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data == "noop":
        pass

    elif data.startswith("add_cart_"):
        product_id = int(data.split("_")[2])
        product = await db.get_product(product_id)
        await db.add_to_cart(user_id, product_id)
        keyboard = await get_product_keyboard(user_id, product_id)
        try:
            await query.edit_message_reply_markup(reply_markup=keyboard)
        except Exception:
            pass

    elif data.startswith("dec_cart_"):
        product_id = int(data.split("_")[2])
        await db.dec_from_cart(user_id, product_id)
        keyboard = await get_product_keyboard(user_id, product_id)
        try:
            await query.edit_message_reply_markup(reply_markup=keyboard)
        except Exception:
            pass

    elif data == "clear_cart":
        await db.clear_cart(user_id)
        await query.edit_message_text("🗑 Savatcha tozalandi.")

    elif data == "checkout":
        cart_items = await db.get_cart(user_id)
        if not cart_items:
            await query.answer("Savatcha bo'sh!", show_alert=True)
            return
        total = sum(item['price'] * item['quantity'] for item in cart_items)
        order_id = await db.create_order(user_id, cart_items, total)
        await db.clear_cart(user_id)

        text = f"✅ *Buyurtma #{order_id} qabul qilindi!*\n\n"
        text += f"💵 Jami: {fmt(total)} $\n"
        text += f"💰 Qarz: {fmt(total)} $\n\n"
        text += "Admin siz bilan bog'lanadi."

        await query.edit_message_text(text, parse_mode='Markdown')

        # Adminlarga xabar
        for admin_id in ADMIN_IDS:
            try:
                user_info = await db.get_user(user_id)
                items_text = ""
                for item in cart_items:
                    items_text += f"  • {item['name']} x{item['quantity']} = {fmt(item['price'] * item['quantity'])} $\n"
                await context.bot.send_message(
                    admin_id,
                    "🛍 *Yangi buyurtma #{}*\n"
                    "👤 {}\n"
                    "📱 {}\n\n"
                    "📦 Mahsulotlar:\n{}\n"
                    "💵 Jami: {} $".format(
                        order_id,
                        user_info['full_name'],
                        user_info.get('phone') or "noma'lum",
                        items_text,
                        fmt(total)
                    ),
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"Admin {admin_id}ga xabar: {e}")

    elif data.startswith("delete_product_"):
        product_id = int(data.split("_")[2])
        await db.delete_product(product_id)
        try:
            if query.message.photo:
                await query.edit_message_caption("🗑 Mahsulot o'chirildi.")
            else:
                await query.edit_message_text("🗑 Mahsulot o'chirildi.")
        except:
            pass

    elif data.startswith("add_debt_"):
        target_user_id = int(data.split("_")[2])
        target_user = await db.get_user(target_user_id)
        context.user_data['add_debt_user_id'] = target_user_id
        context.user_data['add_debt_user_name'] = target_user['full_name']
        await query.edit_message_text(
            "👤 *{}*\n💰 Joriy qarz: *{} $*\n\nQo'shilishi kerak bo'lgan summani kiriting:".format(
                target_user['full_name'], fmt(target_user['total_debt'])
            ),
            parse_mode='Markdown'
        )
        return ADMIN_ADD_DEBT_AMOUNT

    elif data.startswith("reduce_debt_"):
        # Admin mijoz tanladi
        target_user_id = int(data.split("_")[2])
        target_user = await db.get_user(target_user_id)
        context.user_data['reduce_debt_user_id'] = target_user_id
        context.user_data['reduce_debt_user_name'] = target_user['full_name']
        await query.edit_message_text(
            "👤 *{}*\n💰 Joriy qarz: *{} $*\n\nKamaytiriladigan summani kiriting:".format(
                target_user['full_name'], fmt(target_user['total_debt'])
            ),
            parse_mode='Markdown'
        )
        return ADMIN_REDUCE_DEBT_AMOUNT


# --- ADMIN ---

async def handle_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id

    if not is_admin(user_id):
        return MAIN_MENU

    if text == "➕ Mahsulot qo'shish":
        await update.message.reply_text("Mahsulot nomini kiriting:")
        return ADMIN_ADD_NAME
    elif text == "📦 Mahsulotlar":
        return await admin_show_products(update, context)
    elif text == "👥 Mijozlar":
        return await admin_show_clients(update, context)
    elif text == "💳 Qarzlar":
        return await admin_show_debts(update, context)
    elif text == "📋 Buyurtmalar":
        return await admin_show_orders(update, context)
    elif text == "📢 Xabar yuborish":
        await update.message.reply_text(
            "📢 Yubormoqchi bo'lgan xabaringizni yozing:\n\n(Barcha foydalanuvchilarga boradi)"
        )
        return ADMIN_BROADCAST
    elif text == "🔙 Asosiy menyu":
        await update.message.reply_text("Asosiy menyu:", reply_markup=main_menu_keyboard(True))
        return MAIN_MENU
    return ADMIN_MENU


async def admin_show_debts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    debtors = await db.get_all_debtors()
    if not debtors:
        await update.message.reply_text("✅ Qarzlar yo'q!")
        return ADMIN_MENU

    total_debt = sum(d['total_debt'] for d in debtors)
    await update.message.reply_text(
        "💳 *Qarzlar ro'yxati ({} ta mijoz)*\nUmumiy: *{} $*".format(
            len(debtors), fmt(total_debt)
        ),
        parse_mode='Markdown'
    )

    for debtor in debtors:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➖ Qarzni kamaytir", callback_data=f"reduce_debt_{debtor['id']}")]
        ])
        text = "👤 *{}*\n📱 {}\n💰 Qarz: *{} $*".format(
            debtor['full_name'],
            debtor.get('phone') or "noma'lum",
            fmt(debtor['total_debt'])
        )
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=keyboard)

    return ADMIN_MENU


async def admin_reduce_debt_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target_user_id = context.user_data.get('reduce_debt_user_id')
    target_user_name = context.user_data.get('reduce_debt_user_name', '')

    if not target_user_id:
        await update.message.reply_text("❗ Xato. Qaytadan boshlang.", reply_markup=admin_menu_keyboard())
        return ADMIN_MENU

    try:
        amount = round(float(update.message.text.replace(" ", "").replace(",", ".")) * 100)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❗ To'g'ri raqam kiriting (masalan: 5 yoki 1.5):")
        return ADMIN_REDUCE_DEBT_AMOUNT

    if amount <= 0:
        await update.message.reply_text("❗ Miqdor 0 dan katta bo'lishi kerak:")
        return ADMIN_REDUCE_DEBT_AMOUNT

    target_user = await db.get_user(target_user_id)
    if amount > target_user['total_debt']:
        await update.message.reply_text(
            "❗ Qarz miqdori {} $. Undan ko'p kirita olmaysiz:".format(fmt(target_user['total_debt']))
        )
        return ADMIN_REDUCE_DEBT_AMOUNT

    await db.reduce_debt(target_user_id, amount)
    updated_user = await db.get_user(target_user_id)

    context.user_data.pop('reduce_debt_user_id', None)
    context.user_data.pop('reduce_debt_user_name', None)

    await update.message.reply_text(
        "✅ *{}* ning qarzi *{} $* ga kamaytirildi!\n💰 Qolgan qarz: *{} $*".format(
            target_user_name, fmt(amount), fmt(updated_user['total_debt'])
        ),
        parse_mode='Markdown',
        reply_markup=admin_menu_keyboard()
    )

    # Mijozga xabar
    try:
        await context.bot.send_message(
            target_user_id,
            "✅ Sizning qarzingiz *{} $* ga kamaytirildi!\n💰 Qolgan qarz: *{} $*".format(
                fmt(amount), fmt(updated_user['total_debt'])
            ),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Mijozga xabar yuborishda xato: {e}")

    return ADMIN_MENU


async def admin_show_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    products = await db.get_all_products()
    if not products:
        await update.message.reply_text("📦 Hozircha mahsulotlar yo'q.")
        return ADMIN_MENU

    await update.message.reply_text(f"📦 Jami {len(products)} ta mahsulot:")
    for product in products:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑 O'chirish", callback_data=f"delete_product_{product['id']}")]
        ])
        text = f"*{product['name']}*\n💵 {fmt(product['price'])} $"
        if product['photo_id']:
            await update.message.reply_photo(product['photo_id'], caption=text, parse_mode='Markdown', reply_markup=keyboard)
        else:
            await update.message.reply_text(text, parse_mode='Markdown', reply_markup=keyboard)
    return ADMIN_MENU


async def admin_show_clients(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = await db.get_all_users()
    if not users:
        await update.message.reply_text("👥 Mijozlar yo'q.")
        return ADMIN_MENU

    text = "👥 *Mijozlar ({} ta):*\n\n".format(len(users))
    await update.message.reply_text(text, parse_mode='Markdown')

    for user in users:
        debt = user.get('total_debt', 0) or 0
        debt_text = "💰 Qarz: {} $".format(fmt(debt)) if debt > 0 else "Qarz yo'q"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Qarz qo'sh", callback_data="add_debt_{}".format(user['id']))]
        ])
        user_text = "👤 *{}*\n📱 {}\n{}".format(
            user['full_name'],
            user.get('phone') or "noma'lum",
            debt_text
        )
        await update.message.reply_text(user_text, parse_mode='Markdown', reply_markup=keyboard)

    return ADMIN_MENU


async def admin_show_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    orders = await db.get_all_orders()
    if not orders:
        await update.message.reply_text("📋 Buyurtmalar yo'q.")
        return ADMIN_MENU

    await update.message.reply_text(f"📋 So'nggi {len(orders)} ta buyurtma:")
    for order in orders:
        text = "#{} — 👤 {}\n💵 {} $ | 💰 Qarz: {} $\n📅 {}".format(
            order['id'],
            order['full_name'],
            fmt(order['total_amount']),
            fmt(order['debt_amount']),
            order['created_at'].strftime('%d.%m.%Y %H:%M')
        )
        await update.message.reply_text(text)
    return ADMIN_MENU


async def admin_add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_product'] = {'name': update.message.text}
    await update.message.reply_text(
        f"✅ Nom: *{update.message.text}*\n\nNarxni kiriting ($da):",
        parse_mode='Markdown'
    )
    return ADMIN_ADD_PRICE


async def admin_add_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price_str = update.message.text.replace(" ", "").replace(",", ".")
        price = round(float(price_str) * 100)  # sentlarda saqlash
        if price <= 0:
            raise ValueError
        context.user_data['new_product']['price'] = price
        display_price = price / 100
        await update.message.reply_text(
            "✅ Narx: *{:.2f} $*\n\nRasmini yuboring yoki /skip bosing:".format(display_price),
            parse_mode='Markdown'
        )
        return ADMIN_ADD_PHOTO
    except ValueError:
        await update.message.reply_text("❗ To'g'ri narx kiriting (masalan: 1.5 yoki 0.9):")
        return ADMIN_ADD_PRICE


async def admin_add_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo:
        context.user_data['new_product']['photo_id'] = update.message.photo[-1].file_id
    else:
        context.user_data['new_product']['photo_id'] = None
    return await show_product_confirm(update, context)


async def skip_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'new_product' not in context.user_data:
        return ADMIN_MENU
    context.user_data['new_product']['photo_id'] = None
    return await show_product_confirm(update, context)


async def show_product_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    product = context.user_data['new_product']
    text = "📦 *Mahsulot ma'lumotlari:*\n\nNom: *{}*\nNarx: *{} $*".format(
        product['name'], fmt(product['price'])
    )
    if not product.get('photo_id'):
        text += "\nRasm: yo'q"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Saqlash", callback_data="save_product"),
         InlineKeyboardButton("❌ Bekor", callback_data="cancel_product")]
    ])
    if product.get('photo_id'):
        await update.message.reply_photo(product['photo_id'], caption=text, parse_mode='Markdown', reply_markup=keyboard)
    else:
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=keyboard)
    return ADMIN_ADD_CONFIRM


async def admin_product_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "save_product":
        product = context.user_data.get('new_product', {})
        if not product:
            await query.edit_message_text("❗ Xato. Qaytadan boshlang.")
            return ADMIN_MENU
        await db.add_product(product['name'], product['price'], product.get('photo_id'))
        try:
            if query.message.photo:
                await query.edit_message_caption("✅ Mahsulot saqlandi!")
            else:
                await query.edit_message_text("✅ Mahsulot saqlandi!")
        except:
            pass
        context.user_data.pop('new_product', None)
    elif query.data == "cancel_product":
        try:
            await query.edit_message_text("❌ Bekor qilindi.")
        except:
            pass
        context.user_data.pop('new_product', None)
    return ADMIN_MENU


async def admin_add_debt_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target_user_id = context.user_data.get('add_debt_user_id')
    target_user_name = context.user_data.get('add_debt_user_name', '')

    if not target_user_id:
        await update.message.reply_text("❗ Xato. Qaytadan boshlang.", reply_markup=admin_menu_keyboard())
        return ADMIN_MENU

    try:
        amount = round(float(update.message.text.replace(" ", "").replace(",", ".")) * 100)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❗ To'g'ri raqam kiriting (masalan: 5 yoki 1.5):")
        return ADMIN_ADD_DEBT_AMOUNT

    if amount <= 0:
        await update.message.reply_text("❗ Miqdor 0 dan katta bo'lishi kerak:")
        return ADMIN_ADD_DEBT_AMOUNT

    await db.add_debt(target_user_id, amount)
    updated_user = await db.get_user(target_user_id)

    context.user_data.pop('add_debt_user_id', None)
    context.user_data.pop('add_debt_user_name', None)

    await update.message.reply_text(
        "✅ *{}* nomiga *{} $* qarz qo'shildi!\n💰 Jami qarz: *{} $*".format(
            target_user_name, fmt(amount), fmt(updated_user['total_debt'])
        ),
        parse_mode='Markdown',
        reply_markup=admin_menu_keyboard()
    )

    # Mijozga xabar
    try:
        await context.bot.send_message(
            target_user_id,
            "📋 Sizning hisobingizga *{} $* qarz qo'shildi.\n💰 Jami qarz: *{} $*".format(
                fmt(amount), fmt(updated_user['total_debt'])
            ),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Mijozga xabar yuborishda xato: {e}")

    return ADMIN_MENU



class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args):
        pass

def run_health_server():
    port = int(os.getenv("PORT", 10000))
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()


async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    users = await db.get_all_users()

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Yuborish", callback_data="broadcast_confirm"),
            InlineKeyboardButton("❌ Bekor", callback_data="broadcast_cancel")
        ]
    ])

    context.user_data['broadcast_text'] = text
    await update.message.reply_text(
        "📢 *Xabar ko'rinishi:*\n\n{}\n\n👥 {} ta foydalanuvchiga yuboriladi.\nTasdiqlaysizmi?".format(
            text, len(users)
        ),
        parse_mode='Markdown',
        reply_markup=keyboard
    )
    return ADMIN_BROADCAST


async def broadcast_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "broadcast_cancel":
        await query.edit_message_text("❌ Bekor qilindi.")
        context.user_data.pop('broadcast_text', None)
        return ADMIN_MENU

    text = context.user_data.get('broadcast_text')
    if not text:
        await query.edit_message_text("❗ Xato. Qaytadan boshlang.")
        return ADMIN_MENU

    users = await db.get_all_users()
    success = 0
    failed = 0

    await query.edit_message_text("⏳ Yuborilmoqda...")

    for user in users:
        try:
            await context.bot.send_message(user['id'], text)
            success += 1
        except Exception:
            failed += 1

    context.user_data.pop('broadcast_text', None)
    await query.edit_message_text(
        "✅ Xabar yuborildi!\n👥 Muvaffaqiyatli: {} ta\n❌ Yuborilmadi: {} ta".format(
            success, failed
        )
    )
    return ADMIN_MENU


async def post_init(application: Application):
    await db.connect()
    logger.info("✅ Ma'lumotlar bazasi ulandi")


def main():
    threading.Thread(target=run_health_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CLIENT_PHONE: [
                MessageHandler(filters.CONTACT, handle_phone),
            ],
            MAIN_MENU: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_main_menu),
                CallbackQueryHandler(callback_handler),
            ],
            CATALOG: [
                CallbackQueryHandler(callback_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_main_menu),
            ],
            CART: [
                CallbackQueryHandler(callback_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_main_menu),
            ],
            ADMIN_MENU: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_menu),
                CallbackQueryHandler(admin_product_callback, pattern="^(save_product|cancel_product)$"),
                CallbackQueryHandler(callback_handler),
            ],
            ADMIN_ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_name)],
            ADMIN_ADD_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_price)],
            ADMIN_ADD_PHOTO: [
                MessageHandler(filters.PHOTO, admin_add_photo),
                CommandHandler("skip", skip_photo),
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_photo),
            ],
            ADMIN_ADD_CONFIRM: [
                CallbackQueryHandler(admin_product_callback, pattern="^(save_product|cancel_product)$"),
                CallbackQueryHandler(callback_handler),
            ],
            ADMIN_REDUCE_DEBT_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_reduce_debt_amount),
                CallbackQueryHandler(callback_handler),
            ],
            ADMIN_ADD_DEBT_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_debt_amount),
                CallbackQueryHandler(callback_handler),
            ],
            ADMIN_BROADCAST: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast),
                CallbackQueryHandler(broadcast_callback, pattern="^broadcast_"),
            ],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True,
    )

    app.add_handler(conv_handler)

    logger.info("🚀 Bot ishga tushdi...")
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
