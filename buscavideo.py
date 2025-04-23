import os
import sys
import sqlite3
import re
import logging
import asyncio
from telegram import Update, constants, ReplyKeyboardMarkup, BotCommand, MenuButtonCommands
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ConversationHandler,
)

# Configurações do bot
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")  # Defina a variável de ambiente TELEGRAM_BOT_TOKEN
if not BOT_TOKEN:
    logging.error("Token do bot não definido. Use a variável de ambiente TELEGRAM_BOT_TOKEN.")
    sys.exit(1)

CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID", "-1001234567890"))  # ID do grupo para notificações
DB_PATH = "produtos.db"
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "SUA_SENHA_ADMIN")  # Defina ADMIN_PASSWORD no .env ou export

# Estados para ConversationHandler não-admin
(MENU, SEARCH_ENTRY, ID_ENTRY) = range(3)
# Estados para ConversationHandler admin
(ADMIN_PASS, ADMIN_ACTION,
 ADD_ID, ADD_NAME, ADD_URL,
 EDIT_ID, EDIT_NAME, EDIT_URL,
 REM_ID) = range(3, 3+9)

# Regex para ID ABC-123-2DS
ID_PATTERN = re.compile(r'^[A-Z0-9]{3}-[A-Z0-9]{3}-[A-Z0-9]{3}$')

# Logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Inicializa DB
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        '''CREATE TABLE IF NOT EXISTS produtos (
            id TEXT PRIMARY KEY,
            nome TEXT NOT NULL,
            url TEXT NOT NULL)''')
    conn.commit()
    conn.close()

async def run_db(fn, *args):
    return await asyncio.to_thread(fn, *args)

# Operações de DB síncronas
def _insert(prod_id, nome, url):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT INTO produtos(id,nome,url) VALUES(?,?,?)", (prod_id, nome, url))
    conn.commit()
    conn.close()

def _update(prod_id, nome, url):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE produtos SET nome=?,url=? WHERE id=?", (nome, url, prod_id))
    conn.commit()
    conn.close()

def _delete(prod_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM produtos WHERE id=?", (prod_id,))
    conn.commit()
    conn.close()

def _fetch_by_id(prod_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT nome,url FROM produtos WHERE id=?", (prod_id,))
    res = cur.fetchone()
    conn.close()
    return res

def _search(keywords):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    parts = keywords.split()
    sql = "SELECT nome,url FROM produtos WHERE " + " AND ".join(["nome LIKE '%'||?||'%'" for _ in parts])
    cur.execute(sql, parts)
    res = cur.fetchall()
    conn.close()
    return res

# Handlers não-admin
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    keyboard = [["ID"], ["Buscar produto"], ["/cancel"]]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    mensagem = (
        "👋 *Bem-vindo!*\n"
        "Escolha: \n"
        "• /obter_id ou ID (formato ABC-123-2DS)\n"
        "• /buscar ou Buscar produto\n"
        "• /cancel para sair"
    )
    await update.message.reply_text(mensagem, parse_mode=constants.ParseMode.MARKDOWN_V2, reply_markup=markup)
    return MENU

async def cmd_buscar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("🔍 Digite as palavras-chave:")
    return SEARCH_ENTRY

async def cmd_obter_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("🔎 Digite o ID (formato ABC-123-2DS):")
    return ID_ENTRY

async def menu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "ID":
        return await cmd_obter_id(update, context)
    if text == "Buscar produto":
        return await cmd_buscar(update, context)
    await update.message.reply_text("Opção inválida. Selecione no menu.")
    return MENU

async def search_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keywords = update.message.text.strip()
    rows = await run_db(_search, keywords)
    if rows:
        for nome, url in rows[:5]:
            await update.message.reply_text(f"*{nome}*\n{url}", parse_mode=constants.ParseMode.MARKDOWN_V2)
    else:
        await update.message.reply_text("❌ Nenhum produto encontrado.")
    return MENU

async def show_by_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pid = update.message.text.strip().upper()
    if not ID_PATTERN.match(pid):
        await update.message.reply_text("ID inválido. Formato ABC-123-2DS.")
        return ID_ENTRY
    res = await run_db(_fetch_by_id, pid)
    if res:
        nome, url = res
        await update.message.reply_text(f"*{nome}*\n{url}", parse_mode=constants.ParseMode.MARKDOWN_V2)
    else:
        await update.message.reply_text("❌ Produto não encontrado.")
    return MENU

# Handlers admin
async def admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("🔒 Informe a senha de admin:")
    return ADMIN_PASS

async def admin_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip() != ADMIN_PASSWORD:
        await update.message.reply_text("❌ Senha incorreta. /cancel para sair.")
        return ADMIN_PASS
    keyboard = [["Adicionar"], ["Editar"], ["Remover"], ["/cancel"]]
    await update.message.reply_text(
        "✅ Acesso liberado. Escolha a ação:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return ADMIN_ACTION

async def admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    action = update.message.text.strip().lower()
    if action == "adicionar":
        await update.message.reply_text("✏️ Digite o ID do produto (ABC-123-2DS):")
        return ADD_ID
    if action == "editar":
        await update.message.reply_text("✏️ Digite o ID do produto a editar:")
        return EDIT_ID
    if action == "remover":
        await update.message.reply_text("🗑️ Digite o ID do produto a remover:")
        return REM_ID
    await update.message.reply_text("Opção inválida. Escolha: Adicionar, Editar ou Remover.")
    return ADMIN_ACTION

async def add_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pid = update.message.text.strip().upper()
    if not ID_PATTERN.match(pid):
        await update.message.reply_text("ID inválido. Formato ABC-123-2DS.")
        return ADD_ID
    context.user_data['admin_id'] = pid
    await update.message.reply_text("Digite o nome do produto:")
    return ADD_NAME

async def add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['admin_name'] = update.message.text.strip()
    await update.message.reply_text("Digite a URL do produto:")
    return ADD_URL

async def add_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    await run_db(_insert, context.user_data['admin_id'], context.user_data['admin_name'], url)
    await update.message.reply_text("✅ Produto adicionado com sucesso.")
    return MENU

async def edit_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pid = update.message.text.strip().upper()
    if not ID_PATTERN.match(pid):
        await update.message.reply_text("ID inválido.")
        return EDIT_ID
    context.user_data['admin_id'] = pid
    await update.message.reply_text("Digite o novo nome do produto:")
    return EDIT_NAME

async def edit_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['admin_name'] = update.message.text.strip()
    await update.message.reply_text("Digite a nova URL do produto:")
    return EDIT_URL

async def edit_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    await run_db(_update, context.user_data['admin_id'], context.user_data['admin_name'], url)
    await update.message.reply_text("✅ Produto atualizado com sucesso.")
    return MENU

async def rem_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pid = update.message.text.strip().upper()
    if not ID_PATTERN.match(pid):
        await update.message.reply_text("ID inválido.")
        return REM_ID
    await run_db(_delete, pid)
    await update.message.reply_text("✅ Produto removido com sucesso.")
    return MENU

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Operação cancelada. Voltando ao menu.")
    return MENU

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Erro: %s", context.error)

async def setup_menu(application):
    commands = [
        BotCommand("obter_id", "ID"),
        BotCommand("buscar", "Buscar produto"),
        BotCommand("adicionar", "Adicionar produto (admin)"),
        BotCommand("editar", "Editar produto (admin)"),
        BotCommand("remover", "Remover produto (admin)")
    ]
    await application.bot.set_my_commands(commands)
    await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())


def main():
    init_db()
    app = ApplicationBuilder() \
        .token(BOT_TOKEN) \
        .post_init(setup_menu) \
        .build()

    # Conversa não-admin
    nonadmin_conv = ConversationHandler(
        entry_points=[
            CommandHandler('start', start),
            CommandHandler('buscar', cmd_buscar),
            CommandHandler('obter_id', cmd_obter_id),
            MessageHandler(filters.Regex('^(ID|Buscar produto)$'), start)
        ],
        states={
            MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, menu_choice)],
            SEARCH_ENTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, search_entry)],
            ID_ENTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, show_by_id)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        allow_reentry=True
    )
    app.add_handler(nonadmin_conv)

    # Comandos diretos admin
    app.add_handler(CommandHandler('adicionar', admin_start))
    app.add_handler(CommandHandler('editar', admin_start))
    app.add_handler(CommandHandler('remover', admin_start))

    # Conversa admin completa
    admin_conv = ConversationHandler(
        entry_points=[CommandHandler('admin', admin_start)],
        states={
            ADMIN_PASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_pass)],
            ADMIN_ACTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_action)],
            ADD_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_id)],
            ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_name)],
            ADD_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_url)],
            EDIT_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_id)],
            EDIT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_name)],
            EDIT_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_url)],
            REM_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, rem_id)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        allow_reentry=True
    )
    app.add_handler(admin_conv)

    app.add_error_handler(error_handler)
    logger.info("Bot iniciado com sucesso.")
    app.run_polling()

if __name__ == '__main__':
    main()
