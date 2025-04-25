import os
import sys
import sqlite3
import re
import logging
import asyncio
from datetime import datetime
from telegram import Update, constants, ReplyKeyboardMarkup, BotCommand, MenuButtonCommands, BotCommandScopeDefault, BotCommandScopeChatMember
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ConversationHandler,
)

# Ativar logging básico e definir logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configurações do bot
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    logger.error("Variável de ambiente TELEGRAM_BOT_TOKEN não encontrada.")
    sys.exit(1)

# IDs e senhas
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "SUA_SENHA_ADMIN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))           # usuário que recebe os comandos admin
ADMIN_GROUP_ID = -1002563145936        # grupo de notificação aos admins

# Banco de dados
DB_PATH = "videos.db"

# Inicialização do banco
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        '''CREATE TABLE IF NOT EXISTS videos (
            id TEXT PRIMARY KEY,
            link TEXT
        )''')
    # nova tabela para registrar a fila de pedidos
    cur.execute(
        '''CREATE TABLE IF NOT EXISTS request_log (
            rowid INTEGER PRIMARY KEY AUTOINCREMENT,
            vid TEXT,
            user TEXT,
            ts DATETIME DEFAULT CURRENT_TIMESTAMP
        )''')
    conn.commit()
    conn.close()

async def run_db(fn, *args):
    return await asyncio.to_thread(fn, *args)

# Operações DB

def _insert(id, link=None):
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("INSERT OR REPLACE INTO videos(id,link) VALUES(?,?)", (id, link))
        conn.commit()
    except Exception as e:
        logger.error(f"Erro ao inserir no banco: {e}")
    finally:
        conn.close()

# registra cada pedido no request_log

def _log_request(vid, user):
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("INSERT INTO request_log(vid, user) VALUES(?,?)", (vid, user))
        conn.commit()
    except Exception as e:
        logger.error(f"Erro ao logar pedido: {e}")
    finally:
        conn.close()


def _fetch_by_id(id):
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT link FROM videos WHERE id=?", (id,))
        row = cur.fetchone()
        return row[0] if row else None
    except Exception as e:
        logger.error(f"Erro ao buscar ID {id} no banco: {e}")
        return None
    finally:
        conn.close()

# buscar fila de pedidos pendentes (sem link)
def _fetch_queue():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        '''SELECT r.vid, r.user, r.ts
           FROM request_log r
           LEFT JOIN videos v ON r.vid = v.id
           WHERE v.link IS NULL
           ORDER BY r.ts'''
    )
    rows = cur.fetchall()
    conn.close()
    return rows

# Regex para padrão ID (11 chars, duas hyphens)
ID_PATTERN = re.compile(r'^[A-Za-z0-9]{3}-[A-Za-z0-9]{3}-[A-Za-z0-9]{3}$')

# Commands configurados por escopo (nomes em lowercase)
USER_COMMANDS = [
    BotCommand("buscarid", "Buscar vídeo por ID"),
    BotCommand("buscarpalavra", "Buscar vídeo por palavra-chave"),
]
ADMIN_COMMANDS = USER_COMMANDS + [
    BotCommand("adicionar", "Adicionar link de vídeo"),
    BotCommand("editar", "Editar link existente"),
    BotCommand("remover", "Remover vídeo"),
    BotCommand("fila", "Mostrar fila de pedidos"),  # novo comando
]

# Estados conversas
(MENU, ENTRY_SEARCH, ENTRY_ID) = range(3)
(ADMIN_PASS, ADMIN_ACTION, ADD_ID, ADD_LINK, EDIT_ID, EDIT_LINK, REM_ID) = range(3, 10)

# Decorator para limpar contexto ao iniciar fluxo
def clear_data(func):
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data.clear()
        return await func(update, context)
    return wrapped

# Handler global de ID (escuta todas as mensagens)
async def handle_id_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().upper()
    if not ID_PATTERN.match(text):
        return
    vid = text
    link = await run_db(_fetch_by_id, vid)
    user = update.effective_user
    name = user.username or user.first_name

    if link:
        await update.message.reply_text(f"🔗 Link: {link}")
    else:
        # registra e loga pedido
        await run_db(_insert, vid)
        await run_db(_log_request, vid, name)
        await update.message.reply_text("✅ ID registrado! O link estará disponível em breve.")
        if ADMIN_GROUP_ID:
            msg = f"Usuário {name} pediu vídeo com o ID {vid}."
            await context.bot.send_message(ADMIN_GROUP_ID, msg)

# Handlers público
@clear_data
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["/buscarid"], ["/buscarpalavra"], ["/cancel"]]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    text = update.message.text.strip().lower()
    if text == "/buscarid":
        return await buscar_por_id(update, context)
    if text == "/buscarpalavra":
        return await buscar_por_palavra(update, context)

    await update.message.reply_text(
        "👋 *Modo Normal*\nEscolha:\n"
        "• /buscarid\n"
        "• /buscarpalavra\n"
        "• /cancel para sair",
        parse_mode=constants.ParseMode.MARKDOWN_V2,
        reply_markup=markup
    )
    return MENU

@clear_data
async def buscar_por_palavra(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Digite palavras-chave:")
    return ENTRY_SEARCH

@clear_data
async def buscar_por_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔎 Digite o ID (formato ABC-123-2DS):")
    return ENTRY_ID

async def menu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()
    if text == "/buscarid":
        return await buscar_por_id(update, context)
    if text == "/buscarpalavra":
        return await buscar_por_palavra(update, context)
    await update.message.reply_text("Opção inválida. Selecione no menu.")
    return MENU

async def entry_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚧 Esta funcionalidade ainda não está disponível.")
    return MENU

async def entry_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    vid = update.message.text.strip().upper()
    if not ID_PATTERN.match(vid):
        await update.message.reply_text("ID inválido. Formato ABC-123-2DS.")
        return ENTRY_ID
    link = await run_db(_fetch_by_id, vid)
    user = update.effective_user
    name = user.username or user.first_name

    if link:
        await update.message.reply_text(f"🔗 Link: {link}")
    else:
        await run_db(_insert, vid)
        await run_db(_log_request, vid, name)
        await update.message.reply_text("✅ ID registrado! O link estará disponível em breve.")
        if ADMIN_GROUP_ID:
            msg = f"Usuário {name} pediu vídeo com o ID {vid}."
            await context.bot.send_message(ADMIN_GROUP_ID, msg)
    return MENU

# Novo handler para comando /fila
async def list_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await run_db(_fetch_queue)
    if not rows:
        await update.message.reply_text("📭 Não há pedidos pendentes.")
    else:
        text = "📋 Fila de pedidos (sem link):\n"
        for vid, user, ts in rows:
            ts_fmt = datetime.fromisoformat(ts).strftime("%d/%m %H:%M")
            text += f"• {ts_fmt} - {user}: {vid}\n"
        await update.message.reply_text(text)
    return ADMIN_ACTION

# Handlers admin
@clear_data
async def admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔒 *Modo Admin*\nInforme a senha:", parse_mode=constants.ParseMode.MARKDOWN_V2)
    return ADMIN_PASS

async def admin_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip() != ADMIN_PASSWORD:
        await update.message.reply_text("❌ Senha incorreta. Tente novamente.")
        return ADMIN_PASS
    keyboard = [["/adicionar"], ["/editar"], ["/remover"], ["/fila"], ["/logout"]]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("✅ Acesso Admin liberado!", reply_markup=markup)
    await context.bot.set_my_commands(ADMIN_COMMANDS, scope=BotCommandScopeChatMember(chat_id=update.effective_chat.id, user_id=update.effective_user.id))
    return ADMIN_ACTION

async def admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    action = update.message.text.strip().lower()
    if action == "/adicionar":
        return await add_id(update, context)
    if action == "/editar":
        return await edit_id(update, context)
    if action == "/remover":
        return await rem_id(update, context)
    if action == "/fila":
        return await list_queue(update, context)
    if action == "/logout":
        await context.bot.set_my_commands(USER_COMMANDS, scope=BotCommandScopeChatMember(chat_id=update.effective_chat.id, user_id=update.effective_user.id))
        await update.message.reply_text("🔄 Saindo do modo Admin.")
        return ConversationHandler.END
    await update.message.reply_text("Opção inválida.")
    return ADMIN_ACTION

async def add_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    vid = update.message.text.strip().upper()
    if not ID_PATTERN.match(vid):
        await update.message.reply_text("ID inválido.")
        return ADD_ID
    context.user_data['aid'] = vid
    await update.message.reply_text("📎 Agora digite o link:")
    return ADD_LINK

async def add_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link = update.message.text.strip()
    vid = context.user_data['aid']
    await run_db(_insert, vid, link)
    await update.message.reply_text("✅ Link adicionado com sucesso.")
    return admin_start(update, context)

async def edit_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    vid = update.message.text.strip().upper()
    if not ID_PATTERN.match(vid):
        await update.message.reply_text("ID inválido.")
        return EDIT_ID
    context.user_data['eid'] = vid
    await update.message.reply_text("✏️ Digite o novo link:")
    return EDIT_LINK

async def edit_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link = update.message.text.strip()
    vid = context.user_data['eid']
    await run_db(_insert, vid, link)
    await update.message.reply_text("✅ Link atualizado.")
    return admin_start(update, context)

async def rem_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    vid = update.message.text.strip().upper()
    if not ID_PATTERN.match(vid):
        await update.message.reply_text("ID inválido.")
        return REM_ID
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM videos WHERE id=?", (vid,))
    conn.commit()
    conn.close()
    await update.message.reply_text("✅ Vídeo removido.")
    return admin_start(update, context)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Operação cancelada.")
    return ConversationHandler.END

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Erro: %s", context.error)

# Configura comandos iniciais
async def setup_menu(application):
    bot = application.bot
    await bot.set_my_commands(USER_COMMANDS, scope=BotCommandScopeDefault())
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())


def main():
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(setup_menu).build()

    # Handler global de ID
    app.add_handler(MessageHandler(filters.Regex(ID_PATTERN), handle_id_request), group=0)

    # Conversa público
    public_conv = ConversationHandler(
        entry_points=[
            CommandHandler('start', start),
            CommandHandler('buscarid', buscar_por_id),
            CommandHandler('buscarpalavra', buscar_por_palavra),
            MessageHandler(filters.Regex('^(buscarid|buscarpalavra)$'), start)
        ],
        states={
            MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, menu_choice)],
            ENTRY_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, entry_search)],
            ENTRY_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, entry_id)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        allow_reentry=True
    )
    app.add_handler(public_conv)

    # Conversa admin
    admin_conv = ConversationHandler(
        entry_points=[CommandHandler('admin', admin_start)],
        states={
            ADMIN_PASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_pass)],
            ADMIN_ACTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_action)],
            ADD_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_id)],
            ADD_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_link)],
            EDIT_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_id)],
            EDIT_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_link)],
            REM_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, rem_id)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        allow_reentry=True
    )
    app.add_handler(admin_conv)

    app.add_error_handler(error_handler)
    app.run_polling()


if __name__ == '__main__':
    main()
