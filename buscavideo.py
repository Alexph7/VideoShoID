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

# States para ConversationHandlers
(ADD_PASS, ADD_ID, ADD_NAME, ADD_URL,
 EDIT_PASS, EDIT_ID, EDIT_NAME, EDIT_URL,
 REM_PASS, REM_ID) = range(10)

# Regex para ID no formato XXX-XXX-XXX
ID_PATTERN = re.compile(r'^[A-Z0-9]{3}-[A-Z0-9]{3}-[A-Z0-9]{3}$')

# Setup de logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Inicializa banco de dados
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS produtos (
            id TEXT PRIMARY KEY,
            nome TEXT NOT NULL,
            url TEXT NOT NULL
        )
        '''
    )
    conn.commit()
    conn.close()

# Executa funções de banco de forma assíncrona
def run_db(fn, *args):
    return asyncio.to_thread(fn, *args)

# Operações no DB
def _insert(prod_id, nome, url):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT INTO produtos (id,nome,url) VALUES (?,?,?)", (prod_id, nome, url))
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
    cur.execute("SELECT nome, url FROM produtos WHERE id=?", (prod_id,))
    res = cur.fetchone()
    conn.close()
    return res


def _search(keywords):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    parts = keywords.split()
    sql = "SELECT nome, url FROM produtos WHERE " + " AND ".join(["nome LIKE '%'||?||'%'" for _ in parts])
    cur.execute(sql, parts)
    res = cur.fetchall()
    conn.close()
    return res

# Handlers comuns
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        ['Consulta por ID'],
        ['/buscar', '/adicionar'],
        ['/editar', '/remover']
    ]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    texto = (
        "📋 *Menu Bot de Produtos* 📋\n\n"
        "• Digite o ID (XXX-XXX-XXX) diretamente para consulta.\n"
        "• /buscar <palavras-chave> para pesquisar.\n"
        "• Comandos admin: /adicionar, /editar, /remover"
    )
    await update.message.reply_text(texto, parse_mode=constants.ParseMode.MARKDOWN_V2, reply_markup=markup)

async def buscar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Use: /buscar <palavras-chave>")
    rows = await run_db(_search, ' '.join(context.args))
    if rows:
        for nome, url in rows[:5]:
            await update.message.reply_text(f"*{nome}*\n{url}", parse_mode=constants.ParseMode.MARKDOWN_V2)
    else:
        await update.message.reply_text("❌ Nenhum produto encontrado.")

async def handle_id_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().upper()
    if ID_PATTERN.match(text):
        res = await run_db(_fetch_by_id, text)
        if res:
            nome, url = res
            await update.message.reply_text(f"*{nome}*\n{url}", parse_mode=constants.ParseMode.MARKDOWN_V2)
        else:
            await update.message.reply_text("❌ Produto não encontrado.")

# Fluxo adicionar (admin)
async def add_start(update, context):
    await update.message.reply_text("🔒 Senha de admin:")
    return ADD_PASS

async def add_pass(update, context):
    if update.message.text.strip() != ADMIN_PASSWORD:
        await update.message.reply_text("❌ Senha incorreta.")
        return ConversationHandler.END
    await update.message.reply_text("Etapa 1: ID do produto (XXX-XXX-XXX)")
    return ADD_ID

async def add_id(update, context):
    pid = update.message.text.strip().upper()
    if not ID_PATTERN.match(pid):
        await update.message.reply_text("ID inválido. Tente novamente.")
        return ADD_ID
    context.user_data['pid'] = pid
    await update.message.reply_text("Etapa 2: Nome do produto")
    return ADD_NAME

async def add_name(update, context):
    context.user_data['pname'] = update.message.text.strip()
    await update.message.reply_text("Etapa 3: URL do produto")
    return ADD_URL

async def add_url(update, context):
    url = update.message.text.strip()
    pid = context.user_data['pid']
    pname = context.user_data['pname']
    try:
        await run_db(_insert, pid, pname, url)
        await update.message.reply_text(f"✅ Produto {pname} adicionado.")
        await context.bot.send_message(CHAT_ID, f"Novo produto: {pid} - {pname} - {url}")
    except sqlite3.IntegrityError:
        await update.message.reply_text("❌ ID já existe.")
    return ConversationHandler.END

# Fluxo editar (admin)
async def edit_start(update, context):
    await update.message.reply_text("🔒 Senha de admin:")
    return EDIT_PASS

async def edit_pass(update, context):
    if update.message.text.strip() != ADMIN_PASSWORD:
        await update.message.reply_text("❌ Senha incorreta.")
        return ConversationHandler.END
    await update.message.reply_text("Etapa 1: ID do produto a editar")
    return EDIT_ID

async def edit_id(update, context):
    pid = update.message.text.strip().upper()
    row = await run_db(_fetch_by_id, pid)
    if not row:
        await update.message.reply_text("ID não encontrado.")
        return ConversationHandler.END
    context.user_data['pid'] = pid
    await update.message.reply_text(f"Atual: {row[0]} - {row[1]}\nEnvie novo NOME:")
    return EDIT_NAME

async def edit_name(update, context):
    context.user_data['pname'] = update.message.text.strip()
    await update.message.reply_text("Envie nova URL:")
    return EDIT_URL

async def edit_url(update, context):
    url = update.message.text.strip()
    pid = context.user_data['pid']
    pname = context.user_data['pname']
    await run_db(_update, pid, pname, url)
    await update.message.reply_text(f"✅ Produto {pid} atualizado.")
    await context.bot.send_message(CHAT_ID, f"Editado: {pid} - {pname} - {url}")
    return ConversationHandler.END

# Fluxo remover (admin)
async def rem_start(update, context):
    await update.message.reply_text("🔒 Senha de admin:")
    return REM_PASS

async def rem_pass(update, context):
    if update.message.text.strip() != ADMIN_PASSWORD:
        await update.message.reply_text("❌ Senha incorreta.")
        return ConversationHandler.END
    await update.message.reply_text("Envie o ID do produto a remover")
    return REM_ID

async def rem_id(update, context):
    pid = update.message.text.strip().upper()
    row = await run_db(_fetch_by_id, pid)
    if not row:
        await update.message.reply_text("ID não encontrado.")
        return ConversationHandler.END
    await run_db(_delete, pid)
    await update.message.reply_text(f"✅ Produto {pid} removido.")
    await context.bot.send_message(CHAT_ID, f"Removido: {pid} - {row[0]}")
    return ConversationHandler.END

async def cancel(update, context):
    await update.message.reply_text("❌ Operação cancelada.")
    return ConversationHandler.END

async def error_handler(update, context):
    logger.error("Erro:", exc_info=context.error)

# Setup do menu sanduíche
async def setup_menu(application):
    commands = [
        BotCommand("buscar", "Buscar produtos"),
        BotCommand("adicionar", "Adicionar produto (admin)"),
        BotCommand("editar", "Editar produto (admin)"),
        BotCommand("remover", "Remover produto (admin)")
    ]
    await application.bot.set_my_commands(commands)
    await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())


def main():
    init_db()
    app = ApplicationBuilder()  \
        .token(BOT_TOKEN)     \
        .post_init(setup_menu) \
        .build()

    # Registra handlers
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('buscar', buscar))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_id_query))

    add_conv = ConversationHandler(
        entry_points=[CommandHandler('adicionar', add_start)],
        states={
            ADD_PASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_pass)],
            ADD_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_id)],
            ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_name)],
            ADD_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_url)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    edit_conv = ConversationHandler(
        entry_points=[CommandHandler('editar', edit_start)],
        states={
            EDIT_PASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_pass)],
            EDIT_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_id)],
            EDIT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_name)],
            EDIT_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_url)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    rem_conv = ConversationHandler(
        entry_points=[CommandHandler('remover', rem_start)],
        states={
            REM_PASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, rem_pass)],
            REM_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, rem_id)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    app.add_handler(add_conv)
    app.add_handler(edit_conv)
    app.add_handler(rem_conv)

    app.add_error_handler(error_handler)
    logger.info("Bot iniciado com menu customizado e menu sanduíche configurado...")
    app.run_polling()

if __name__ == '__main__':
    main()