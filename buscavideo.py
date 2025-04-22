import sqlite3
import re
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# Configurações do bot
BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
DB_PATH = "produtos.db"

# Inicializa logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Regex para ID no formato GDF-OIU-FGH
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


def query_by_id(prod_id: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT nome, url FROM produtos WHERE id = ?", (prod_id,))
    result = cursor.fetchone()
    conn.close()
    return result


def query_by_name(keywords: str):
    words = keywords.split()
    sql = "SELECT nome, url FROM produtos WHERE " + " AND ".join(
        ["nome LIKE '%' || ? || '%'" for _ in words]
    )
    params = words
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(sql, params)
    results = cursor.fetchall()
    conn.close()
    return results


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Olá! Envie o ID do produto (formato GDF-OIU-FGH) ou uma palavra-chave para buscar.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # Verifica se é ID ou busca por nome
    if ID_PATTERN.match(text):
        res = query_by_id(text)
        if res:
            nome, url = res
            await update.message.reply_text(f"*{nome}*\n{url}", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ Produto não encontrado para esse ID.")
    else:
        results = query_by_name(text)
        if results:
            replies = [f"*{nome}*\n{url}" for nome, url in results]
            # Se muitos resultados, limite a, por exemplo, 5 primeiros
            for reply in replies[:5]:
                await update.message.reply_text(reply, parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ Nenhum produto encontrado para essa busca.")


def main():
    # Inicializa o banco
    init_db()

    # Cria a aplicação do Telegram
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Inicia o bot
    logger.info("Bot iniciado...")
    app.run_polling()


if __name__ == '__main__':
    main()
