import logging
import asyncio
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)
import sqlite3
import os

# ─── تنظیمات ───────────────────────────────────────────────────────────────────
BOT_TOKEN = "8266869344:AAENdFNCoefMXmFXxvcKI6sBqyQYS5By2lo"  # توکن ربات خود را اینجا بگذارید

# کانال‌های اجباری (برای دریافت ۱۰ سکه)
MANDATORY_CHANNELS = [
    {"id": "@ARADES_WebForge", "url": "https://t.me/ARADES_WebForge", "name": "ARADES WebForge"},
    {"id": "@ARADESASA",        "url": "https://t.me/ARADESASA",        "name": "ARADESASA"},
    {"id": "@ARADES777",        "url": "https://t.me/ARADES777",        "name": "ARADES777"},
]

COINS_PER_CHANNEL    = 1    # سکه به ازای هر کانال تبلیغاتی
MANDATORY_COINS      = 10   # سکه به ازای هر کانال اجباری
COINS_PER_MEMBER     = 2    # هزینه هر عضو

# حالت‌های مکالمه
(
    WAITING_CHANNEL_USERNAME,
    WAITING_MEMBER_COUNT,
    WAITING_CONFIRM_CHANNEL,
) = range(3)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ─── دیتابیس ───────────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id     INTEGER PRIMARY KEY,
            username    TEXT,
            coins       INTEGER DEFAULT 0,
            joined_mandatory TEXT DEFAULT ''
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS ad_channels (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id   TEXT UNIQUE,
            channel_name TEXT,
            owner_id     INTEGER,
            members_needed INTEGER,
            members_joined INTEGER DEFAULT 0,
            active       INTEGER DEFAULT 1
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS join_logs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER,
            channel_id TEXT,
            UNIQUE(user_id, channel_id)
        )
    """)

    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row

def ensure_user(user_id, username=""):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO users (user_id, username) VALUES (?,?)",
        (user_id, username or "")
    )
    conn.commit()
    conn.close()

def get_coins(user_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT coins FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0

def add_coins(user_id, amount):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("UPDATE users SET coins=coins+? WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()

def deduct_coins(user_id, amount):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("UPDATE users SET coins=coins-? WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()

def get_joined_mandatory(user_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT joined_mandatory FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row and row[0]:
        return set(row[0].split(","))
    return set()

def mark_mandatory_joined(user_id, channel_id):
    joined = get_joined_mandatory(user_id)
    joined.add(channel_id)
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute(
        "UPDATE users SET joined_mandatory=? WHERE user_id=?",
        (",".join(joined), user_id)
    )
    conn.commit()
    conn.close()

def has_joined_channel(user_id, channel_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute(
        "SELECT id FROM join_logs WHERE user_id=? AND channel_id=?",
        (user_id, channel_id)
    )
    row = c.fetchone()
    conn.close()
    return row is not None

def log_join(user_id, channel_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    try:
        c.execute(
            "INSERT INTO join_logs (user_id, channel_id) VALUES (?,?)",
            (user_id, channel_id)
        )
        conn.commit()
        inserted = True
    except sqlite3.IntegrityError:
        inserted = False
    conn.close()
    return inserted

def get_active_ad_channels():
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute(
        "SELECT id, channel_id, channel_name, members_needed, members_joined FROM ad_channels WHERE active=1"
    )
    rows = c.fetchall()
    conn.close()
    return rows

def add_ad_channel(channel_id, channel_name, owner_id, members_needed):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    try:
        c.execute(
            "INSERT INTO ad_channels (channel_id, channel_name, owner_id, members_needed) VALUES (?,?,?,?)",
            (channel_id, channel_name, owner_id, members_needed)
        )
        conn.commit()
        success = True
    except sqlite3.IntegrityError:
        success = False
    conn.close()
    return success

def increment_channel_member(channel_id):
    """یک عضو اضافه می‌کند و اگر به هدف رسید True برمی‌گرداند + اطلاعات کانال"""
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute(
        "UPDATE ad_channels SET members_joined=members_joined+1 WHERE channel_id=? AND active=1",
        (channel_id,)
    )
    conn.commit()
    c.execute(
        "SELECT members_needed, members_joined, owner_id FROM ad_channels WHERE channel_id=?",
        (channel_id,)
    )
    row = c.fetchone()
    conn.close()
    if row:
        needed, joined, owner_id = row
        if joined >= needed:
            return True, owner_id
    return False, None

def deactivate_channel(channel_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("UPDATE ad_channels SET active=0 WHERE channel_id=?", (channel_id,))
    conn.commit()
    conn.close()

def channel_exists(channel_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT id FROM ad_channels WHERE channel_id=? AND active=1", (channel_id,))
    row = c.fetchone()
    conn.close()
    return row is not None


# ─── بررسی عضویت ───────────────────────────────────────────────────────────────
async def is_member(bot, user_id, channel_id):
    try:
        member = await bot.get_chat_member(channel_id, user_id)
        return member.status in [
            ChatMember.MEMBER,
            ChatMember.ADMINISTRATOR,
            ChatMember.OWNER,
        ]
    except Exception:
        return False

async def is_bot_admin(bot, channel_id):
    try:
        member = await bot.get_chat_member(channel_id, (await bot.get_me()).id)
        return member.status in [ChatMember.ADMINISTRATOR, ChatMember.OWNER]
    except Exception:
        return False

async def check_all_mandatory(bot, user_id):
    """بررسی می‌کند کاربر عضو همه کانال‌های اجباری هست یا نه"""
    not_joined = []
    for ch in MANDATORY_CHANNELS:
        if not await is_member(bot, user_id, ch["id"]):
            not_joined.append(ch)
    return not_joined


# ─── منوی اصلی ─────────────────────────────────────────────────────────────────
def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 لیست کانال‌ها (کسب سکه)", callback_data="list_channels")],
        [InlineKeyboardButton("➕ ثبت کانال خودم", callback_data="register_channel")],
        [InlineKeyboardButton("📊 آمار (سکه‌های من)", callback_data="stats")],
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username or "")

    # بررسی عضویت اجباری
    not_joined = await check_all_mandatory(context.bot, user.id)
    if not_joined:
        buttons = []
        for ch in not_joined:
            buttons.append([InlineKeyboardButton(f"🔔 عضویت در {ch['name']}", url=ch["url"])])
        buttons.append([InlineKeyboardButton("✅ عضو شدم، بررسی کن", callback_data="check_mandatory")])
        await update.message.reply_text(
            "👋 خوش آمدید!\n\n"
            "⚠️ برای استفاده از ربات، ابتدا باید عضو کانال‌های زیر شوید:\n"
            "(با عضویت در هر کانال ۱۰ سکه هدیه می‌گیرید!)",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    coins = get_coins(user.id)
    await update.message.reply_text(
        f"👋 سلام {user.first_name}!\n\n"
        f"💰 سکه‌های شما: {coins}\n\n"
        "از منوی زیر استفاده کنید:",
        reply_markup=main_menu_keyboard()
    )


# ─── بررسی عضویت اجباری ────────────────────────────────────────────────────────
async def check_mandatory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    ensure_user(user.id, user.username or "")

    already_joined = get_joined_mandatory(user.id)
    newly_joined = []

    for ch in MANDATORY_CHANNELS:
        if ch["id"] not in already_joined:
            if await is_member(context.bot, user.id, ch["id"]):
                mark_mandatory_joined(user.id, ch["id"])
                add_coins(user.id, MANDATORY_COINS)
                newly_joined.append(ch["name"])

    not_joined = await check_all_mandatory(context.bot, user.id)

    if not_joined:
        msg = "❌ هنوز عضو همه کانال‌ها نشده‌اید!\n\n"
        if newly_joined:
            msg += f"✅ کانال‌هایی که تازه عضو شدید: {', '.join(newly_joined)}\n\n"
        msg += "کانال‌های باقی‌مانده:"
        buttons = []
        for ch in not_joined:
            buttons.append([InlineKeyboardButton(f"🔔 {ch['name']}", url=ch["url"])])
        buttons.append([InlineKeyboardButton("✅ بررسی مجدد", callback_data="check_mandatory")])
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        coins = get_coins(user.id)
        msg = "✅ عالی! عضویت شما در همه کانال‌ها تأیید شد!\n"
        if newly_joined:
            msg += f"🎁 {len(newly_joined) * MANDATORY_COINS} سکه دریافت کردید!\n"
        msg += f"\n💰 موجودی فعلی: {coins} سکه"
        await query.edit_message_text(msg, reply_markup=main_menu_keyboard())


# ─── لیست کانال‌های تبلیغاتی ──────────────────────────────────────────────────
async def list_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    # بررسی عضویت اجباری
    not_joined = await check_all_mandatory(context.bot, user.id)
    if not_joined:
        buttons = []
        for ch in not_joined:
            buttons.append([InlineKeyboardButton(f"🔔 {ch['name']}", url=ch["url"])])
        buttons.append([InlineKeyboardButton("✅ بررسی کن", callback_data="check_mandatory")])
        await query.edit_message_text(
            "⚠️ ابتدا باید عضو کانال‌های اجباری شوید!",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    buttons = []
    msg = "📋 *لیست کانال‌ها برای کسب سکه*\n\n"

    # ─── بخش کانال‌های اجباری ───
    msg += f"⭐ *کانال‌های اجباری* — هر کدام {MANDATORY_COINS} سکه:\n"
    joined_mandatory = get_joined_mandatory(user.id)
    for ch in MANDATORY_CHANNELS:
        already = ch["id"] in joined_mandatory
        status = "✅" if already else "🎁"
        label = f"{status} {ch['name']} | {MANDATORY_COINS} سکه"
        if already:
            buttons.append([InlineKeyboardButton(label, callback_data="already_joined")])
        else:
            buttons.append([InlineKeyboardButton(label, url=ch["url"])])

    # ─── بخش کانال‌های تبلیغاتی ───
    channels = get_active_ad_channels()
    if channels:
        msg += f"\n📢 *کانال‌های تبلیغاتی* — هر کدام {COINS_PER_CHANNEL} سکه:\n"
        for ch in channels:
            ch_id, ch_name, _, needed, joined = ch
            remaining = needed - joined
            already = has_joined_channel(user.id, ch_id)
            status = "✅" if already else "🔔"
            label = f"{status} {ch_name} | باقی‌مانده: {remaining} نفر | {COINS_PER_CHANNEL} سکه"
            if not already:
                buttons.append([InlineKeyboardButton(label, callback_data=f"join_ch:{ch_id}")])
            else:
                buttons.append([InlineKeyboardButton(label, callback_data="already_joined")])
    else:
        msg += "\n📢 *کانال‌های تبلیغاتی:*\nدر حال حاضر کانال تبلیغاتی موجود نیست.\n"

    msg += "\n💡 روی هر کانال بزنید تا عضو شوید و سکه بگیرید!"
    buttons.append([InlineKeyboardButton("🔄 بررسی عضویت کانال‌های اجباری", callback_data="check_mandatory")])
    buttons.append([InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")])

    await query.edit_message_text(
        msg,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def join_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    channel_id = query.data.split(":")[1]

    if has_joined_channel(user.id, channel_id):
        await query.answer("✅ قبلاً به این کانال پیوستید!", show_alert=True)
        return

    # ساخت لینک کانال
    ch_link = channel_id if channel_id.startswith("https://") else f"https://t.me/{channel_id.lstrip('@')}"

    buttons = [
        [InlineKeyboardButton("🔔 عضویت در کانال", url=ch_link)],
        [InlineKeyboardButton("✅ عضو شدم، سکه بده!", callback_data=f"verify_join:{channel_id}")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="list_channels")],
    ]
    await query.edit_message_text(
        f"برای دریافت {COINS_PER_CHANNEL} سکه:\n\n"
        "1️⃣ روی دکمه عضویت کلیک کنید\n"
        "2️⃣ عضو کانال شوید\n"
        "3️⃣ سپس 'عضو شدم' را بزنید",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def verify_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    channel_id = query.data.split(":")[1]

    if has_joined_channel(user.id, channel_id):
        await query.answer("✅ قبلاً ثبت شده!", show_alert=True)
        return

    if not await is_member(context.bot, user.id, channel_id):
        await query.answer("❌ هنوز عضو نشده‌اید! ابتدا عضو شوید.", show_alert=True)
        return

    # ثبت عضویت
    inserted = log_join(user.id, channel_id)
    if not inserted:
        await query.answer("✅ قبلاً ثبت شده!", show_alert=True)
        return

    add_coins(user.id, COINS_PER_CHANNEL)

    # افزایش شمارنده کانال
    completed, owner_id = increment_channel_member(channel_id)
    if completed:
        deactivate_channel(channel_id)
        try:
            await context.bot.send_message(
                owner_id,
                f"🎉 سفارش شما برای کانال `{channel_id}` کامل شد!\n"
                "تعداد اعضای درخواستی به کانال شما پیوستند.",
                parse_mode="Markdown"
            )
        except Exception:
            pass

    coins = get_coins(user.id)
    await query.edit_message_text(
        f"✅ عضویت تأیید شد!\n"
        f"🎁 {COINS_PER_CHANNEL} سکه دریافت کردید!\n"
        f"💰 موجودی شما: {coins} سکه",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 کانال‌های بیشتر", callback_data="list_channels")],
            [InlineKeyboardButton("🏠 منوی اصلی", callback_data="back_main")],
        ])
    )


# ─── ثبت کانال ─────────────────────────────────────────────────────────────────
async def register_channel_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    # بررسی عضویت اجباری
    not_joined = await check_all_mandatory(context.bot, user.id)
    if not_joined:
        await query.edit_message_text(
            "⚠️ ابتدا باید عضو کانال‌های اجباری شوید!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ بررسی عضویت", callback_data="check_mandatory")]
            ])
        )
        return

    await query.edit_message_text(
        "➕ *ثبت کانال*\n\n"
        "⚠️ *مهم:* قبل از ثبت کانال، حتماً ربات را به عنوان *مدیر با تمام دسترسی‌ها* به کانال خود اضافه کنید!\n\n"
        "📌 مراحل:\n"
        "1️⃣ ربات را به کانال اضافه کنید\n"
        "2️⃣ به ربات دسترسی *مدیر با تمام اجازه‌ها* بدهید\n"
        "3️⃣ یوزرنیم کانال را اینجا ارسال کنید\n\n"
        "یوزرنیم کانال را ارسال کنید (مثال: @mychannel):\n\n"
        "برای لغو /cancel را بزنید",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ انصراف", callback_data="back_main")]
        ])
    )
    return WAITING_CHANNEL_USERNAME


async def receive_channel_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()

    if not text.startswith("@"):
        text = "@" + text

    # بررسی اینکه ربات ادمین است
    await update.message.reply_text("⏳ در حال بررسی...")

    if not await is_bot_admin(context.bot, text):
        await update.message.reply_text(
            "❌ ربات در این کانال مدیر نیست یا دسترسی کافی ندارد!\n\n"
            "✅ لطفاً:\n"
            "1️⃣ ربات را به کانال اضافه کنید\n"
            "2️⃣ به ربات دسترسی *مدیر با تمام اجازه‌ها* بدهید\n"
            "3️⃣ دوباره امتحان کنید\n\n"
            "یوزرنیم کانال را مجدداً ارسال کنید یا /cancel برای لغو:",
            parse_mode="Markdown"
        )
        return WAITING_CHANNEL_USERNAME

    if channel_exists(text):
        await update.message.reply_text(
            "⚠️ این کانال قبلاً ثبت شده و هنوز فعال است!\n"
            "یوزرنیم کانال دیگری ارسال کنید یا /cancel برای لغو:"
        )
        return WAITING_CHANNEL_USERNAME

    # ذخیره یوزرنیم کانال
    context.user_data["reg_channel"] = text

    try:
        chat = await context.bot.get_chat(text)
        ch_name = chat.title or text
        context.user_data["reg_channel_name"] = ch_name
    except Exception:
        context.user_data["reg_channel_name"] = text

    coins = get_coins(user.id)
    await update.message.reply_text(
        f"✅ کانال پیدا شد: *{context.user_data['reg_channel_name']}*\n\n"
        f"💰 موجودی شما: {coins} سکه\n"
        f"💡 هر عضو = {COINS_PER_MEMBER} سکه\n\n"
        "چند عضو می‌خواهید؟ (عدد بفرستید)\n"
        "مثال: برای ۱۰ عضو، باید ۲۰ سکه داشته باشید:",
        parse_mode="Markdown"
    )
    return WAITING_MEMBER_COUNT


async def receive_member_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()

    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("❌ عدد معتبر وارد کنید (مثلاً: 10)")
        return WAITING_MEMBER_COUNT

    count = int(text)
    cost = count * COINS_PER_MEMBER
    coins = get_coins(user.id)

    context.user_data["reg_member_count"] = count
    context.user_data["reg_cost"] = cost

    if coins < cost:
        await update.message.reply_text(
            f"❌ موجودی کافی نیست!\n\n"
            f"📊 درخواست شما: {count} عضو\n"
            f"💸 هزینه: {cost} سکه\n"
            f"💰 موجودی شما: {coins} سکه\n"
            f"کمبود: {cost - coins} سکه\n\n"
            "عدد کمتری وارد کنید یا /cancel برای لغو:"
        )
        return WAITING_MEMBER_COUNT

    await update.message.reply_text(
        f"📋 *تأیید ثبت کانال*\n\n"
        f"📢 کانال: {context.user_data['reg_channel_name']}\n"
        f"👥 تعداد اعضای درخواستی: {count}\n"
        f"💸 هزینه: {cost} سکه\n"
        f"💰 موجودی شما: {coins} سکه\n\n"
        "آیا تأیید می‌کنید؟",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ تأیید", callback_data="confirm_register"),
                InlineKeyboardButton("❌ انصراف", callback_data="back_main"),
            ]
        ])
    )
    return WAITING_CONFIRM_CHANNEL


async def confirm_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    channel_id   = context.user_data.get("reg_channel")
    channel_name = context.user_data.get("reg_channel_name")
    count        = context.user_data.get("reg_member_count")
    cost         = context.user_data.get("reg_cost")

    if not all([channel_id, count, cost]):
        await query.edit_message_text("❌ خطا! دوباره امتحان کنید.")
        return ConversationHandler.END

    coins = get_coins(user.id)
    if coins < cost:
        await query.edit_message_text(
            "❌ موجودی کافی نیست!",
            reply_markup=main_menu_keyboard()
        )
        return ConversationHandler.END

    # بررسی مجدد ادمین بودن ربات
    if not await is_bot_admin(context.bot, channel_id):
        await query.edit_message_text(
            "❌ ربات دیگر مدیر کانال نیست!\n"
            "دوباره ثبت‌نام کنید.",
            reply_markup=main_menu_keyboard()
        )
        return ConversationHandler.END

    deduct_coins(user.id, cost)
    success = add_ad_channel(channel_id, channel_name, user.id, count)

    if not success:
        add_coins(user.id, cost)  # برگشت سکه
        await query.edit_message_text(
            "⚠️ این کانال قبلاً ثبت شده!",
            reply_markup=main_menu_keyboard()
        )
        return ConversationHandler.END

    await query.edit_message_text(
        f"✅ *کانال با موفقیت ثبت شد!*\n\n"
        f"📢 کانال: {channel_name}\n"
        f"👥 اعضای درخواستی: {count}\n"
        f"💸 هزینه پرداخت شده: {cost} سکه\n\n"
        "کانال شما در لیست قرار گرفت و کاربران شروع به عضویت می‌کنند!",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )
    context.user_data.clear()
    return ConversationHandler.END


# ─── آمار ──────────────────────────────────────────────────────────────────────
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    coins = get_coins(user.id)
    joined_mandatory = get_joined_mandatory(user.id)

    # تعداد کانال‌های تبلیغاتی که عضو شده
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM join_logs WHERE user_id=?", (user.id,))
    ad_joined = c.fetchone()[0]
    conn.close()

    mandatory_text = ""
    for ch in MANDATORY_CHANNELS:
        status = "✅" if ch["id"] in joined_mandatory else "❌"
        mandatory_text += f"  {status} {ch['name']}\n"

    await query.edit_message_text(
        f"📊 *آمار شما*\n\n"
        f"💰 موجودی سکه: {coins}\n"
        f"📋 کانال‌های تبلیغاتی که عضو شدید: {ad_joined}\n\n"
        f"📌 کانال‌های اجباری:\n{mandatory_text}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")]
        ])
    )


# ─── هندلرهای کمکی ─────────────────────────────────────────────────────────────
async def back_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    coins = get_coins(user.id)
    await query.edit_message_text(
        f"🏠 منوی اصلی\n💰 سکه‌های شما: {coins}",
        reply_markup=main_menu_keyboard()
    )

async def already_joined_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("✅ قبلاً به این کانال پیوستید!", show_alert=True)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "❌ عملیات لغو شد.",
        reply_markup=main_menu_keyboard()
    )
    return ConversationHandler.END


# ─── اجرای ربات ────────────────────────────────────────────────────────────────
def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # ConversationHandler برای ثبت کانال
    reg_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(register_channel_start, pattern="^register_channel$")],
        states={
            WAITING_CHANNEL_USERNAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_channel_username)
            ],
            WAITING_MEMBER_COUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_member_count)
            ],
            WAITING_CONFIRM_CHANNEL: [
                CallbackQueryHandler(confirm_register, pattern="^confirm_register$"),
                CallbackQueryHandler(back_main, pattern="^back_main$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(reg_conv)
    app.add_handler(CallbackQueryHandler(check_mandatory,   pattern="^check_mandatory$"))
    app.add_handler(CallbackQueryHandler(list_channels,     pattern="^list_channels$"))
    app.add_handler(CallbackQueryHandler(join_channel,      pattern="^join_ch:"))
    app.add_handler(CallbackQueryHandler(verify_join,       pattern="^verify_join:"))
    app.add_handler(CallbackQueryHandler(stats,             pattern="^stats$"))
    app.add_handler(CallbackQueryHandler(back_main,         pattern="^back_main$"))
    app.add_handler(CallbackQueryHandler(already_joined_cb, pattern="^already_joined$"))

    print("✅ ربات شروع به کار کرد...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
