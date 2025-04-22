import sqlite3
import re
import logging
import asyncio
from telegram import Update, constants
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters, CallbackContext, Application,
)

# Configurações do bot
BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
CHAT_ID = -1001234567890  # ID do chat para notificações, se necessário
DB_PATH = "produtos.db"

# Inicializa logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Regex para ID no formato XXX-XXX-XXX
ID_PATTERN = re.compile(r'^[A-Z0-9]{3}-[A-Z0-9]{3}-[A-Z0-9]{3}$')


def init_db():
    """
    Cria a tabela de produtos se não existir.
    """
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


def _query_by_id(prod_id: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT nome, url FROM produtos WHERE id = ?", (prod_id,))
    result = cursor.fetchone()
    conn.close()
    return result


def _query_by_name(keywords: str):
    words = keywords.split()
    sql = ("SELECT nome, url FROM produtos WHERE " +
           " AND ".join(["nome LIKE '%' || ? || '%'" for _ in words]))
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(sql, words)
    results = cursor.fetchall()
    conn.close()
    return results


async def query_by_id(prod_id: str):
    # Executa operação de I/O em thread separado
    return await asyncio.to_thread(_query_by_id, prod_id)


async def query_by_name(keywords: str):
    return await asyncio.to_thread(_query_by_name, keywords)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Olá! Envie o ID do produto (formato XXX-XXX-XXX) para verificar se existe. "
        "Para buscar por palavras-chave, use: /buscar <palavras-chave>."
    )


async def buscar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text(
            "Por favor, forneça palavras-chave após o comando /buscar."
        )

    keywords = ' '.join(context.args)
    results = await query_by_name(keywords)
    if results:
        for nome, url in results[:5]:
            await update.message.reply_text(
                f"*{nome}*\n{url}",
                parse_mode=constants.ParseMode.MARKDOWN_V2
            )
    else:
        await update.message.reply_text(
            "❌ Nenhum produto encontrado para essa busca."
        )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().upper()
    if ID_PATTERN.match(text):
        res = await query_by_id(text)
        if res:
            nome, url = res
            await update.message.reply_text(
                f"*{nome}*\n{url}",
                parse_mode=constants.ParseMode.MARKDOWN_V2
            )
        else:
            await update.message.reply_text(
                "❌ Produto não encontrado com esse ID."
            )
    else:
        # Opcional: ignora ou instrui usuário
        logger.debug(f"Mensagem ignorada (não é ID): {text}")


def error_handler(update: object, context: CallbackContext):
    logger.error(
        msg="Exception while handling an update:",
        exc_info=context.error
    )


def main():
    init_db()

    app: Application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("buscar", buscar))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    app.add_error_handler(error_handler)

    logger.info("Bot iniciado...")
    app.run_polling()


if __name__ == '__main__':
    main()
