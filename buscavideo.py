import sqlite3
import re
import logging
import asyncio
from telegram import Update, constants
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    CallbackContext,
    Application,
    ConversationHandler,
)

# Configurações do bot
BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
CHAT_ID = -1001234567890  # ID do grupo ou canal para notificações
DB_PATH = "produtos.db"

# Estados da conversa de adicionar produto
ID, NAME, URL = range(3)

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


def _insert_product(prod_id: str, nome: str, url: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO produtos (id, nome, url) VALUES (?, ?, ?)",
        (prod_id, nome, url)
    )
    conn.commit()
    conn.close()

async def insert_product(prod_id: str, nome: str, url: str):
    return await asyncio.to_thread(_insert_product, prod_id, nome, url)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Olá! Use /adicionar para incluir um novo produto em etapas, ou /buscar para pesquisar.\n"
        "Envie /cancel para cancelar qualquer ação."
    )

async def buscar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text(
            "Por favor, forneça palavras-chave após /buscar."
        )
    keywords = ' '.join(context.args)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    words = keywords.split()
    sql = ("SELECT nome, url FROM produtos WHERE " +
           " AND ".join(["nome LIKE '%' || ? || '%'" for _ in words]))
    cursor.execute(sql, words)
    results = cursor.fetchall()
    conn.close()

    if results:
        for nome, url in results[:5]:
            await update.message.reply_text(
                f"*{nome}*\n{url}",
                parse_mode=constants.ParseMode.MARKDOWN_V2
            )
    else:
        await update.message.reply_text("❌ Nenhum produto encontrado.")

async def adicionar_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Etapa 1/3: Por favor, envie o ID do produto (formato XXX-XXX-XXX) ou /cancel para sair."
    )
    return ID

async def adicionar_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prod_id = update.message.text.strip().upper()
    if not ID_PATTERN.match(prod_id):
        await update.message.reply_text(
            "❌ ID inválido. Deve ser no formato XXX-XXX-XXX. Tente novamente ou /cancel."
        )
        return ID
    context.user_data['prod_id'] = prod_id
    await update.message.reply_text(
        "Etapa 2/3: Agora envie o NOME do produto ou /cancel."
    )
    return NAME

async def adicionar_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nome = update.message.text.strip()
    context.user_data['nome'] = nome
    await update.message.reply_text(
        "Etapa 3/3: Por fim, envie a URL do produto ou /cancel."
    )
    return URL

async def adicionar_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    prod_id = context.user_data['prod_id']
    nome = context.user_data['nome']
    try:
        await insert_product(prod_id, nome, url)
        # Confirmação ao usuário
        await update.message.reply_text(
            f"✅ Produto *{nome}* (ID: {prod_id}) adicionado com sucesso!",
            parse_mode=constants.ParseMode.MARKDOWN_V2
        )
        # Notificação no grupo
        await context.bot.send_message(
            chat_id=CHAT_ID,
            text=f"🆕 *Novo produto cadastrado!*\n*ID:* {prod_id}\n*Nome:* {nome}\n*URL:* {url}",
            parse_mode=constants.ParseMode.MARKDOWN_V2
        )
    except sqlite3.IntegrityError:
        await update.message.reply_text(
            "❌ Já existe um produto com esse ID. Operação cancelada."
        )
    except Exception as e:
        logger.error("Erro ao inserir produto: %s", e)
        await update.message.reply_text(
            "❌ Erro ao adicionar produto."
        )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❌ Operação cancelada.",
        parse_mode=constants.ParseMode.MARKDOWN
    )
    return ConversationHandler.END

async def error_handler(update: object, context: CallbackContext):
    logger.error("Exception durante o processamento:", exc_info=context.error)


def main():
    init_db()
    app: Application = ApplicationBuilder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('adicionar', adicionar_start)],
        states={
            ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, adicionar_id)],
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, adicionar_name)],
            URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, adicionar_url)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("buscar", buscar))
    app.add_handler(conv_handler)
    app.add_error_handler(error_handler)

    logger.info("Bot iniciado...")
    app.run_polling()

if __name__ == '__main__':
    main()
