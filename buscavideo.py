import os
import sys
import logging
import telegram
from telegram import Update, constants
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

print("» python-telegram-bot version:", telegram.__version__)

# Configura logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    logger.error("Variável de ambiente TELEGRAM_BOT_TOKEN não encontrada.")
    sys.exit(1)


# Função que roda depois que o bot conecta, para ajustar a descrição
async def setup_bot_description(app):
    # descrição curta (topo da conversa)
    await app.bot.set_my_short_description(
        short_description=(
            "🤖 Olá! Sou o bot do @cupomnavitrine – "
            "envie um ID e eu busco o vídeo pra você."
        ),
        language_code="pt"
    )
    # descrição longa (na página do bot)
    await app.bot.set_my_description(
        description=(
            "🤖 Olá! Sou o bot do @cupomnavitrine – "
            "vou te ajudar a buscar vídeos da shopee pra você por IDs. "
            "Se não existir ainda, coloco na fila e aviso quando estiver disponível. 👌"
        ),
        language_code="pt"
    )
    logger.info("Descrições do bot definidas com sucesso.")

# Handler para /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔎 Digite o ID do produto no formato ABC-12G-DX3 ou use /buscar para procurar por nome."
    )

def main():
    # Aqui construímos o app **uma única vez**, incluindo o post_init:
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(setup_bot_description)  # define as descrições ao subir
        .build()
    )

    # Registra o handler de /start
    app.add_handler(CommandHandler('start', start))

    # Inicia o polling
    app.run_polling()

if __name__ == '__main__':
    main()
