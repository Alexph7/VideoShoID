import os
import sys
import logging
import sqlite3
import re
import asyncio
from telegram import BotCommand, BotCommandScopeDefault, Update, InputFile
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters)

# caminho absoluto da pasta onde está este .py
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# caminhos para as imagens
IMG1_PATH = os.path.join(BASE_DIR, "imagens", "passo1.jpg")
IMG2_PATH = os.path.join(BASE_DIR, "imagens", "passo2.jpg")

# ————— Configurações básicas —————
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN não encontrado.")
    sys.exit(1)

# Senha para acessar comandos avançados (só admins sabem)
ADMIN_PASSWORD = 5590
ADMIN_IDS = [6294708048]  # adicione aqui todos os user_id dos seus admins

CANAL_ID = -1002563145936
DB_PATH = "videos.db"

# Estados de conversa
WAITING_FOR_ID, AGUARDANDO_SENHA, WAITING_FOR_NOME_PRODUTO, WAITING_FOR_ID_PRODUTO, WAITING_FOR_LINK_PRODUTO, WAITING_FOR_QUEM = range(1, 7)

ADMIN_MENU = (
    "🔧 *Menu Admin* 🔧\n\n"
    "/adicionar – Adicionar produtos\n"
    "/fila – Listar pedidos pendentes\n"
    "/historico – Ver todos os pedidos\n"
    "/concluidos – Ver apenas pedidos concluídos\n"
    "/rejeitados – Ver apenas pedidos rejeitados\n"
    "/consultar\\_pedido – Ver quem pediu o ID\n"
    "/total\\_pedidos – Ver total de pedidos no banco\n"
)

# Regex para validar ID
ID_PATTERN = re.compile(r'^[A-Za-z0-9]{3}-[A-Za-z0-9]{3}-[A-Za-z0-9]{3}$')

# ————— Funções de banco —————
async def executar_db(fn, *args):
    try:
        return await asyncio.to_thread(fn, *args)
    except Exception:
        logger.exception("Erro na operação de banco em thread")
        return None

def inserir_video(vid, link=None):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("INSERT OR REPLACE INTO videos(id,link) VALUES(?,?)", (vid, link))
        conn.commit()
    finally:
        conn.close()


def buscar_link_por_id(vid):
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute("SELECT link FROM videos WHERE id=?", (vid,))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()

def salvar_pedido_pendente(usuario_id, nome_usuario, video_id, status="pendente", hora_solicitacao=None):
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        if hora_solicitacao:
            cur.execute(
                "INSERT INTO pending_requests (user_id, username, video_id, status, requested_at) VALUES (?, ?, ?, ?, ?)",
                (usuario_id, nome_usuario, video_id, status, hora_solicitacao)
            )
        else:
            cur.execute(
                "INSERT INTO pending_requests (user_id, username, video_id, status) VALUES (?, ?, ?, ?)",
                (usuario_id, nome_usuario, video_id, status)
            )
        conn.commit()
    except Exception as e:
        logger.error(f"Erro ao salvar pedido pendente: {e}")
    finally:
        conn.close()

# Handler para /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔎 Olá para começar, você pode acionar a qualquer momento o comando /busca_id clicando em cima dele ou no menu lateral"
    )

async def iniciar_adicionar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # apenas admins podem adicionar
    if not context.user_data.get("is_admin"):
        await update.message.reply_text("❌ Você não tem permissão para usar /adicionar.")
        return ConversationHandler.END

    # admin: inicia normalmente o fluxo
    await update.message.reply_text("📝 Digite o nome do produto:")
    return WAITING_FOR_NOME_PRODUTO


async def receber_nome_produto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["nome_produto"] = update.message.text.strip()
    await update.message.reply_text("🔢 Agora, digite o ID do produto (formato 123-ABC-X1Z):")
    return WAITING_FOR_ID_PRODUTO

async def receber_id_produto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    vid = update.message.text.strip().upper()
    if not ID_PATTERN.match(vid):
        await update.message.reply_text("❌ ID inválido. Tente novamente no formato correto.")
        return WAITING_FOR_ID_PRODUTO

    context.user_data["id_produto"] = vid
    await update.message.reply_text("🌐 Agora, envie o link do produto:")
    return WAITING_FOR_LINK_PRODUTO

async def receber_link_produto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link = update.message.text.strip()
    nome = context.user_data.get("nome_produto")
    vid = context.user_data.get("id_produto")

    # Salva no banco de dados
    await executar_db(inserir_video, vid, link)

    # Atualiza todos usuários que pediram esse ID
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM pending_requests WHERE video_id = ? AND status = 'pendente'", (vid,))
    usuarios = cur.fetchall()
    conn.close()

    if usuarios:
        for (user_id,) in usuarios:
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"📦 Seu pedido para o ID `{vid}` foi concluído!\n🔗 {link}",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Erro ao enviar mensagem para {user_id}: {e}")

        # Atualiza status para 'concluido'
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "UPDATE pending_requests SET status = 'concluido' WHERE video_id = ? AND status = 'pendente'",
            (vid,)
        )
        conn.commit()
        conn.close()

    await update.message.reply_text("✅ Produto adicionado com sucesso e usuários notificados!")

    context.user_data.clear()
    return ConversationHandler.END


# ————— Funções de notificação —————
async def notificar_canal_admin(context: ContextTypes.DEFAULT_TYPE, user, vid, message):
    try:
        chat_id_str = str(message.chat.id)
        msg_id_str = str(message.message_id)
        internal_chat_id = chat_id_str[4:] if chat_id_str.startswith("-100") else None
        link_mensagem = f"https://t.me/c/{internal_chat_id}/{msg_id_str}" if internal_chat_id else "🔒 (Chat privado)"

        texto = f"📨 Novo pedido de ID\n"
        texto += f"👤 Usuário: {user.username or user.first_name or 'Usuário desconhecido'} (ID: {user.id})\n"
        texto += f"🆔 Pedido: {vid}\n"
        texto += f"🔗 [Ver mensagem]({link_mensagem})\n"

        await context.bot.send_message(chat_id=CANAL_ID, text=texto, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Erro ao enviar notificação para o canal: {e}")

# ————— Conversa /busca_id —————
async def iniciar_busca_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Digite o ID no formato 123-ABC-X1Z ou se não souber como encontrar clique em /ajuda"
    )
    return WAITING_FOR_ID

async def tratar_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    vid = update.message.text.strip().upper()
    if not ID_PATTERN.match(vid):
        await update.message.reply_text(
            "❌ ID inválido. Tente novamente no formato correto."
        )
        return WAITING_FOR_ID

    link = await executar_db(buscar_link_por_id, vid)
    user = update.effective_user
    nome = user.username or user.first_name or "Usuário desconhecido"


    if link:
        await update.message.reply_text(f"🔗 Link encontrado: {link}")
        now = sqlite3.connect(DB_PATH).execute("SELECT CURRENT_TIMESTAMP").fetchone()[0]
        salvar_pedido_pendente(user.id, nome, vid, status="encontrado", hora_solicitacao=now)
    else:
        await executar_db(inserir_video, vid)
        salvar_pedido_pendente(user.id, nome, vid, status="pendente")
        await update.message.reply_text(
            "✅ ID adicionado à fila. Avisarei quando o link estiver disponível."
        )
        await notificar_canal_admin(context, user, vid, update.message)

    return ConversationHandler.END


async def iniciar_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in ADMIN_IDS:
        context.user_data["is_admin"] = True
        await update.message.reply_text(ADMIN_MENU, parse_mode="Markdown")
        return ConversationHandler.END

    await update.message.reply_text("🔒 Digite a senha de admin:")
    return AGUARDANDO_SENHA


async def tratar_senha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip() == str(ADMIN_PASSWORD):
        context.user_data["is_admin"] = True
        await update.message.reply_text(ADMIN_MENU, parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Senha incorreta. Acesso negado.")
    return ConversationHandler.END


# ————— Mostrar fila —————
async def mostrar_fila(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.user_data.get("is_admin"):
        await update.message.reply_text("❌ Você não tem permissão.")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id, username, video_id, requested_at, status FROM pending_requests WHERE status = 'pendente' ORDER BY requested_at ASC")
    rows = cur.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("📭 Nenhum pedido pendente!")
        return

    resposta = "📋 *Pedidos pendentes:*\n\n"
    for i, (user_id, username, video_id, requested_at, status) in enumerate(rows, 1):
        resposta += f"*{i}.* 👤 {username} (`{user_id}`)\n"
        resposta += f"🆔 `{video_id}` — 🕒 `{requested_at}` — 📄 *{status}*\n\n"
    await update.message.reply_text(resposta, parse_mode="Markdown")


# Mostrar histórico completo
async def mostrar_historico(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("is_admin"):
        await update.message.reply_text("❌ Você não tem permissão.")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id, username, video_id, requested_at, status FROM pending_requests ORDER BY requested_at ASC")
    rows = cur.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("📭 Nenhum pedido encontrado!")
        return

    resposta = "📚 *Histórico de todos os pedidos:*\n\n"
    for i, (user_id, username, video_id, requested_at, status) in enumerate(rows, 1):
        resposta += f"*{i}.* 👤 {username} (`{user_id}`)\n"
        resposta += f"🆔 `{video_id}` — 🕒 `{requested_at}` — 📄 *{status}*\n\n"

    await update.message.reply_text(resposta, parse_mode="Markdown")

# Mostrar apenas pedidos concluídos
async def mostrar_concluidos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("is_admin"):
        await update.message.reply_text("❌ Você não tem permissão.")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id, username, video_id, requested_at FROM pending_requests WHERE status = 'concluido' ORDER BY requested_at ASC")
    rows = cur.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("📭 Nenhum pedido concluído!")
        return

    resposta = "✅ *Pedidos concluídos:*\n\n"
    for i, (user_id, username, video_id, requested_at) in enumerate(rows, 1):
        resposta += f"*{i}.* 👤 {username} (`{user_id}`)\n"
        resposta += f"🆔 `{video_id}` — 🕒 `{requested_at}`\n\n"

    await update.message.reply_text(resposta, parse_mode="Markdown")

# Mostrar apenas pedidos rejeitados
async def mostrar_rejeitados(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("is_admin"):
        await update.message.reply_text("❌ Você não tem permissão.")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id, username, video_id, requested_at FROM pending_requests WHERE status = 'rejeitado' ORDER BY requested_at ASC")
    rows = cur.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("📭 Nenhum pedido rejeitado!")
        return

    resposta = "❌ *Pedidos rejeitados:*\n\n"
    for i, (user_id, username, video_id, requested_at) in enumerate(rows, 1):
        resposta += f"*{i}.* 👤 {username} (`{user_id}`)\n"
        resposta += f"🆔 `{video_id}` — 🕒 `{requested_at}`\n\n"

    await update.message.reply_text(resposta, parse_mode="Markdown")


async def mostrar_meus_pedidos(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user
    user_id = user.id

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        SELECT video_id, requested_at, status 
        FROM pending_requests 
        WHERE user_id = ?
        ORDER BY requested_at DESC
    """, (user_id,))

    pedidos = cur.fetchall()
    conn.close()

    if not pedidos:
        await update.message.reply_text("📭 Você ainda não tem pedidos registrados.")
        return

    resposta = "📄 *Seus pedidos anteriores:*\n\n"
    for i, (video_id, requested_at, status) in enumerate(pedidos, 1):
        resposta += f"*{i}.* 🆔 `{video_id}` | 🕒 `{requested_at}` | 📌 *{status}*\n"

    await update.message.reply_text(resposta, parse_mode="Markdown")


# 2) Use só essa função para os dois passos:
async def consultar_pedido(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # 2.1) Só admin pode usar
    if not context.user_data.get("is_admin"):
        return await update.message.reply_text("❌ Você não tem permissão.")

    # 2.2) Se veio com argumento, processa
    if context.args:
        video_id = context.args[0].strip().upper()
    # 2.3) Se não, é porque acabamos de chamar "/quem_pediu" — pedimos o ID
    else:
        await update.message.reply_text(
            "🔍 Diga o ID do vídeo e eu te mostro quem pediu (se existir):"
        )
        return WAITING_FOR_QUEM

    # 3) Aqui cai tanto se veio em context.args quanto se veio pelo MessageHandler
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT user_id, username FROM pending_requests WHERE video_id = ?",
        (video_id,)
    )
    resultado = cur.fetchone()
    conn.close()

    if not resultado:
        await update.message.reply_text("❌ Nenhum pedido encontrado com esse ID.")
    else:
        user_id, username = resultado
        await update.message.reply_text(
            f"🔍 *Informações do pedido*\n"
            f"📽️ ID do vídeo: `{video_id}`\n"
            f"👤 User ID: `{user_id}`\n"
            f"📝 Nome de usuário: `{username or 'Desconhecido'}`",
            parse_mode="Markdown"
        )

    # 4) Limpa o fluxo de conversa (se veio por ele)
    return ConversationHandler.END

# ————— Cancelar conversa —————
async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return ConversationHandler.END

# ————— Configura comandos —————
async def setup_commands(app):
    try:
        await app.bot.set_my_commands(
            [
                BotCommand("busca_id", "Buscar vídeo por ID"),
                BotCommand("meus_pedidos", "Veja seu historico"),
                BotCommand("ajuda", "Como encontrar o ID na Shopee"),
            ],
            scope=BotCommandScopeDefault()
        )
        logger.info("Comandos configurados.")
    except Exception:
        logger.exception("Erro ao configurar comandos")

def init_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        # tabela de administradores dinâmicos
        cur.execute(
            '''CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY
            )'''
        )
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
        cur.execute(
            '''CREATE TABLE IF NOT EXISTS pending_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                username TEXT,
                video_id TEXT,
                requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'esperando'
            )'''
        )
        conn.commit()
    except Exception:
        logger.exception("Erro ao inicializar o banco de dados")
    finally:
        conn.close()

async def ajuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Aqui está como encontrar o ID. Siga os passos abaixo:")


    # Passo 1
    if not os.path.exists(IMG1_PATH):
        logger.warning("Imagem passo1 não encontrada!")
    else:
        with open(IMG1_PATH, "rb") as img1:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=InputFile(img1),
                caption="📌 Passo 1: Escolha o Produto e Clique em Compartilhar."
            )

    # Passo 2
    if not os.path.exists(IMG2_PATH):
        logger.warning("Imagem passo2 não encontrada!")
    else:
        with open(IMG2_PATH, "rb") as img2:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=InputFile(img2),
                caption="📌 Passo 2: Copie o ID mostrado no Formato indicado. \n\n 👉~Acione o comando /busca_id e cole o código"
            )


async def mostrar_total_pedidos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Apenas admins
    if not context.user_data.get("is_admin"):
        await update.message.reply_text("❌ Você não tem permissão para usar este comando.")
        return

    # Conecta ao banco e conta todos os pedidos
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM pending_requests")
    total = cur.fetchone()[0]
    conn.close()

    await update.message.reply_text(f"📊 Total de pedidos registrados no banco: {total}")

def load_admins_from_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM admins")
    rows = [r[0] for r in cur.fetchall()]
    conn.close()
    return rows

def inserir_admin_db(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("INSERT OR IGNORE INTO admins(user_id) VALUES(?)", (user_id,))
        conn.commit()
    finally:
        conn.close()


async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
        # 1) só admin pode usar
        if not context.user_data.get("is_admin"):
            return await update.message.reply_text("❌ Você não tem permissão para isso.")

        # 2) pega o argumento
        if not context.args:
            return await update.message.reply_text("Use: /addadmin <user_id>")

        try:
            novo_id = int(context.args[0])
        except ValueError:
            return await update.message.reply_text("❌ ID inválido. Passe um número de usuário válido.")

        # 3) insere no DB e na lista em memória
        inserir_admin_db(novo_id)
        if novo_id not in ADMIN_IDS:
            ADMIN_IDS.append(novo_id)

        await update.message.reply_text(f"✅ Usuário `{novo_id}` adicionado como admin.", parse_mode="Markdown")


# ————— Ponto de entrada —————
if __name__ == "__main__":
    init_db()
    # carrega do DB
    dynamic_admins = load_admins_from_db()
    # mescla com os admins fixos que você colocava manualmente
    ADMIN_IDS = list(set(ADMIN_IDS + dynamic_admins))

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(setup_commands)
        .build()
    )

    # Conversation handler principal, incluindo /adicionar e menu admin
    main_conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("busca_id", iniciar_busca_id),
            CommandHandler("admin", iniciar_admin),
            CommandHandler("ajuda", ajuda),
            CommandHandler("meus_pedidos", mostrar_meus_pedidos),
        ],
        states={
            WAITING_FOR_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, tratar_id),
                CommandHandler("busca_id", iniciar_busca_id),
            ],
            AGUARDANDO_SENHA: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, tratar_senha),
                CommandHandler("admin", iniciar_admin),
            ],
            WAITING_FOR_NOME_PRODUTO: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receber_nome_produto),
            ],
            WAITING_FOR_ID_PRODUTO: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receber_id_produto),
            ],
            WAITING_FOR_LINK_PRODUTO: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receber_link_produto),
            ],
            WAITING_FOR_QUEM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, consultar_pedido),
            ],
        },
        fallbacks=[ CommandHandler("cancelar", cancelar)],
        allow_reentry=True,
        conversation_timeout=259200
    )

    app.add_handler(main_conv)
    admin_handlers = [
        CommandHandler("adicionar", iniciar_adicionar),
        CommandHandler("fila", mostrar_fila),
        CommandHandler("historico", mostrar_historico),
        CommandHandler("concluidos", mostrar_concluidos),
        CommandHandler("rejeitados", mostrar_rejeitados),
        CommandHandler("consultar_pedido", consultar_pedido),
        CommandHandler("total_pedidos", mostrar_total_pedidos),
        CommandHandler("addadmin", add_admin)
    ]
    for handler in admin_handlers:
        app.add_handler(handler)
    app.run_polling()