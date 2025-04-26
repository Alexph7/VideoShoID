import os
import sys
import logging
import sqlite3
import re
import asyncio
from telegram import BotCommand, BotCommandScopeDefault, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# ————— Configurações básicas —————
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Token do bot (via variável de ambiente)
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN não encontrado.")
    sys.exit(1)

# Caminho do banco de dados
DB_PATH = "videos.db"

# Estado da conversa
WAITING_FOR_ID = 1

# Regex para padrão 3 chars, hífen, 3 chars, hífen, 3 chars (letras ou dígitos)
ID_PATTERN = re.compile(r'^[A-Za-z0-9]{3}-[A-Za-z0-9]{3}-[A-Za-z0-9]{3}$')

# Inicializa o banco, criando tabela de vídeos e log de pedidos
def init_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            '''CREATE TABLE IF NOT EXISTS videos (
                id TEXT PRIMARY KEY,
                link TEXT
            )'''
        )
        cur.execute(
            '''CREATE TABLE IF NOT EXISTS request_log (
                rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                vid TEXT,
                user TEXT,
                ts DATETIME DEFAULT CURRENT_TIMESTAMP
            )'''
        )
        conn.commit()
    except Exception:
        logger.exception("Erro ao inicializar o banco de dados")
    finally:
        conn.close()

# Utilitário para executar funções de bd sem bloquear o loop
async def run_db(fn, *args):
    try:
        return await asyncio.to_thread(fn, *args)
    except Exception:
        logger.exception("Erro na operação de banco em thread")
        return None

# Funções de acesso ao SQLite
def _insert(vid, link=None):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("INSERT OR REPLACE INTO videos(id,link) VALUES(?,?)", (vid, link))
        conn.commit()
    finally:
        conn.close()

def _log_request(vid, user):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("INSERT INTO request_log(vid,user) VALUES(?,?)", (vid, user))
        conn.commit()
    finally:
        conn.close()

def _fetch_by_id(vid):
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute("SELECT link FROM videos WHERE id=?", (vid,))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()

# Início da conversa: comando /busca_id
async def start_busca(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Comando /busca_id acionado por %s", update.effective_user.id)
    await update.message.reply_text(
        "Digite o ID no formato 123-ABC-X1Z (3 caracteres, hífen, 3 caracteres, hífen, 3 caracteres)."
    )
    return WAITING_FOR_ID

# Recebe o ID digitado pelo usuário
async def handle_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    vid = update.message.text.strip().upper()
    # Valida formato
    if not ID_PATTERN.match(vid):
        await update.message.reply_text(
            "❌ ID inválido. Use 3 letras ou dígitos, hífen, 3 letras ou dígitos, hífen, 3 letras ou dígitos. Tente novamente."
        )
        return WAITING_FOR_ID

    # Consulta no banco
    link = await run_db(_fetch_by_id, vid)
    user = update.effective_user
    name = user.username or user.first_name

    if link:
        await update.message.reply_text(f"🔗 Link encontrado: {link}")
    else:
        # Insere pedido e log
        await run_db(_insert, vid)
        await run_db(_log_request, vid, name)
        await update.message.reply_text(
            "✅ ID adicionado à fila. Avisarei quando o link estiver disponível."
        )

    return ConversationHandler.END

# Cancela (opcional)
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operação cancelada.")
    return ConversationHandler.END

# Configura comandos no menu
async def setup_commands(app):
    try:
        await app.bot.set_my_commands(
            [BotCommand("busca_id", "Buscar vídeo por ID interativo")],
            scope=BotCommandScopeDefault()
        )
        logger.info("Comando /busca_id configurado no menu.")
    except Exception:
        logger.exception("Erro ao configurar comandos do bot")

# Monta e executa o bot
if __name__ == "__main__":
    init_db()
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(setup_commands)
        .build()
    )

    # ConversationHandler que reinicia se /busca_id for chamado em qualquer estado
    conv = ConversationHandler(
        entry_points=[CommandHandler("busca_id", start_busca)],
        states={
            WAITING_FOR_ID: [
                # Novo comando reinicia o fluxo
                CommandHandler("busca_id", start_busca),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_id),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.run_polling()
