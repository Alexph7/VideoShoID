import sys
import psycopg2.extras
import re
import os
import logging
import asyncio
from dotenv import load_dotenv
from telegram import BotCommand, BotCommandScopeDefault, Update, InputFile
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters)

load_dotenv()

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

def get_conn_pg():
    host = os.getenv("POSTGRES_HOST")
    port = os.getenv("POSTGRES_PORT")
    db   = os.getenv("POSTGRES_DB")
    user = os.getenv("POSTGRES_USER")
    pwd  = os.getenv("POSTGRES_PASSWORD")
    cursor_factory = psycopg2.extras.RealDictCursor

    # validação
    if not all([host, port, db, user, pwd]):
        logger.error("Variáveis POSTGRES_* não totalmente configuradas.")
        sys.exit(1)

    return psycopg2.connect(
        host=host,
        port=port,
        dbname=db,
        user=user,
        password=pwd,
        cursor_factory=psycopg2.extras.RealDictCursor
    )


 # Senha para acessar comandos avançados (só admins sabem)
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
if ADMIN_IDS_STR:
    try:
        ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_STR.split(',') if x.strip()]
    except ValueError:
        logger.error("ADMIN_IDS deve conter apenas números separados por vírgula.")
        ADMIN_IDS = []
else:
    ADMIN_IDS = []
TELEGRAM_CHAT_ID = (os.getenv("CANAL_ID"))

def buscar_todos_do_banco(query: str, params: tuple = ()):
    """
    Abre conexão, executa SELECT e retorna todos os registros
    """
    conn = get_conn_pg()
    try:
        cur = conn.cursor()
        cur.execute(query, params)
        return cur.fetchall()
    finally:
        conn.close()


def buscar_um_do_banco(query: str, params: tuple = ()):
    conn = get_conn_pg()
    try:
        cur = conn.cursor()
        cur.execute(query, params)
        return cur.fetchone()
    finally:
        conn.close()


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

def inserir_video(vid, link=None):
    with get_conn_pg() as conn:  # usa o 'with' para a conexão
        with conn.cursor() as cur:  # usa o 'with' para o cursor
            if link is not None:
                # insere ou atualiza o link se já existir id igual
                cur.execute(
                    """
                    INSERT INTO videos (id, link)
                    VALUES (%s, %s)
                    ON CONFLICT (id) DO UPDATE
                      SET link = EXCLUDED.link
                    """,
                    (vid, link)
                )
            else:
                cur.execute(# insere só o id se link for None (não substitui nada se já existir)
                    """
                    INSERT INTO videos (id)
                    VALUES (%s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (vid,)
                )
        conn.commit()  # commit continua necessário

async def executar_db(fn, *args):
    try:
        return await asyncio.to_thread(fn, *args)
    except Exception:
        logger.exception("Erro na operação de banco em thread")
        return None

def buscar_link_por_id(vid):
    with get_conn_pg() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT link FROM videos WHERE id=%s", (vid,))
            row = cur.fetchone()
            return row["link"] if row else None


def salvar_pedido_pendente(usuario_id, nome_usuario, video_id, status="pendente"):
    try:
        with get_conn_pg() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO pending_requests 
                      (user_id, username, video_id, status) 
                    VALUES (%s, %s, %s, %s)
                    """,
                    (usuario_id, nome_usuario, video_id, status)
                )
            conn.commit()
    except Exception as e:
        logger.error(f"Erro ao salvar pedido pendente: {e}")

# Mensagem de Mural de Entrada
async def configurar_mural(app):
    await app.bot.set_my_description(
        description=(
            "Olá, Bem-Vindo! 🤖\n\n"
            "Sou um bot desenvolvido por t.me/cupomnavitrine, "
            "estou aqui para te ajudar a criar seu vídeo com o produto da Shopee."
        )
    )
    await app.bot.set_my_short_description(
        short_description="Bot para criar vídeo de produto da Shopee 🎬"
    )

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

    # Agora, buscamos usuários e atualizamos status com uma única conexão
    with get_conn_pg() as conn:
        with conn.cursor() as cur:
            # Buscar usuários com pedidos pendentes para esse vídeo
            cur.execute(
                "SELECT user_id FROM pending_requests WHERE video_id = %s AND status = 'pendente'",
                (vid,)
            )
            usuarios = cur.fetchall()

        for row in usuarios:
            user_id = row["user_id"]
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"📦 Seu pedido para o ID `{vid}` foi concluído!\n🔗 {link}",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Erro ao enviar mensagem para {user_id}: {e}")

        with conn.cursor() as cur:
            # Atualiza o status dos pedidos para "concluido"
            cur.execute(
                "UPDATE pending_requests SET status = 'concluido' WHERE video_id = %s AND status = 'pendente'",
                (vid,)
            )
        conn.commit()

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

        await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=texto, parse_mode="Markdown")
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

    # 1) Tenta buscar link existente
    link = await executar_db(buscar_link_por_id, vid)
    user = update.effective_user
    nome = user.username or user.first_name or "Usuário desconhecido"

    if link:
        # 2a) Se existe, responde com o link e registra pedido como 'encontrado'
        await update.message.reply_text(f"🔗 Link encontrado: {link}")
        await executar_db(
            salvar_pedido_pendente,user.id,nome,vid,"encontrado"
        )
    else:
        # 2b) Se não existe, insere novo vídeo sem link e registra como 'pendente'
        await executar_db(inserir_video, vid)
        await executar_db(
            salvar_pedido_pendente,user.id,nome,vid,"pendente"
        )
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

    rows = await asyncio.to_thread(
        buscar_todos_do_banco,
        "SELECT user_id, username, video_id, requested_at, status "
        "FROM pending_requests WHERE status = 'pendente' ORDER BY requested_at ASC"
    )

    if not rows:
        await update.message.reply_text("📭 Nenhum pedido pendente!")
        return

    resposta = "📋 *Pedidos pendentes:*\n\n"
    for i, row in enumerate(rows, 1):
        user_id = row["user_id"]
        username = row["username"]
        video_id = row["video_id"]
        requested_at = row["requested_at"]
        status = row["status"]
        resposta += f"*{i}.* 👤 {username} (`{user_id}`)\n"
        resposta += f"🆔 `{video_id}` — 🕒 `{requested_at}` — *{status}*\n\n"
    await update.message.reply_text(resposta, parse_mode="Markdown")


# Mostrar histórico completo
async def mostrar_historico(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("is_admin"):
        await update.message.reply_text("❌ Você não tem permissão.")
        return

    # Executa a consulta em thread separada
    rows = await asyncio.to_thread(
        buscar_todos_do_banco,
        "SELECT user_id, username, video_id, requested_at, status "
        "FROM pending_requests ORDER BY requested_at ASC"
    )

    if not rows:
        await update.message.reply_text("📭 Nenhum pedido encontrado!")
        return

    resposta = ["📚 *Histórico de todos os pedidos:*",""]
    for i, row in enumerate(rows, 1):
        resposta.append(f"*{i}.* 👤 {row['username']} (`{row['user_id']}`)")
        resposta.append(f"🆔 `{row['video_id']}` — 🕒 `{row['requested_at']}` — 📄 *{row['status']}*")
        resposta.append("")

    await update.message.reply_text("\n".join(resposta), parse_mode="Markdown")

# Mostrar apenas pedidos concluídos
async def mostrar_concluidos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("is_admin"):
        await update.message.reply_text("❌ Você não tem permissão.")
        return

    # busca em outra thread usando nossa função auxiliar
    rows = await asyncio.to_thread(
        buscar_todos_do_banco,
        "SELECT user_id, username, video_id, requested_at "
        "FROM pending_requests WHERE status = 'concluido' ORDER BY requested_at ASC"
    )

    if not rows:
        await update.message.reply_text("📭 Nenhum pedido concluído!")
        return

    resposta = ["✅ *Pedidos concluídos:*", ""]
    for i, row in enumerate(rows, 1):
        resposta.append(f"*{i}.* 👤 {row['username']} (`{row['user_id']}`)")
        resposta.append(f"🆔 `{row['video_id']}` — 🕒 `{row['requested_at']}`")
        resposta.append("")

    await update.message.reply_text("\n".join(resposta), parse_mode="Markdown")

# Mostrar apenas pedidos rejeitados
async def mostrar_rejeitados(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("is_admin"):
        await update.message.reply_text("❌ Você não tem permissão.")
        return

    # busca em outra thread usando nossa função auxiliar
    rows = await asyncio.to_thread(
        buscar_todos_do_banco,
        "SELECT user_id, username, video_id, requested_at "
        "FROM pending_requests WHERE status = 'rejeitado' ORDER BY requested_at ASC"
    )

    if not rows:
        await update.message.reply_text("📭 Nenhum pedido rejeitado!")
        return

    resposta = ["❌ *Pedidos rejeitados:*", ""]
    for i, row in enumerate(rows, 1):
        resposta.append(f"*{i}.* 👤 {row['username']} (`{row['user_id']}`)")
        resposta.append(f"🆔 `{row['video_id']}` — 🕒 `{row['requested_at']}`")
        resposta.append("")

    await update.message.reply_text("\n".join(resposta), parse_mode="Markdown")


async def mostrar_meus_pedidos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    # roda a consulta em outra thread usando nossa função auxiliar
    pedidos = await asyncio.to_thread(
        buscar_todos_do_banco,
        """
        SELECT video_id, requested_at, status 
        FROM pending_requests 
        WHERE user_id = %s
        ORDER BY requested_at DESC
        """,
        (user_id,)
    )

    if not pedidos:
        await update.message.reply_text("📭 Você ainda não tem pedidos registrados.")
        return

    resposta = ["📄 *Seus pedidos anteriores:*", ""]
    for i, row in enumerate(pedidos, 1):
        resposta.append(
            f"*{i}.* 🆔 `{row['video_id']}` | 🕒 `{row['requested_at']}` | 📌 *{row['status']}*"
        )

    await update.message.reply_text("\n".join(resposta), parse_mode="Markdown")


# 2) Use só essa função para os dois passos:
async def consultar_pedido(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    # 1) Só admin pode usar
    if not context.user_data.get("is_admin"):
        await update.message.reply_text("❌ Você não tem permissão.")
        return ConversationHandler.END

    # 2) Tratamento de argumentos
    if context.args:
        if len(context.args) != 1:
            await update.message.reply_text("❌ Use: /consultar_pedido <ID_do_vídeo>")
            return ConversationHandler.END

        video_id = context.args[0].strip().upper()

    # 3) Fluxo de conversa (caso venha sem argumento)
    else:
        await update.message.reply_text(
            "🔍 Diga o ID do vídeo e eu te mostro quem pediu (se existir):"
        )
        return WAITING_FOR_QUEM

    # 4) Busca no banco
    resultado = await asyncio.to_thread(
        buscar_um_do_banco,
        "SELECT user_id, username FROM pending_requests WHERE video_id = %s",
        (video_id,)
    )

    # 5) Resposta
    if not resultado:
        await update.message.reply_text("❌ Nenhum pedido encontrado com esse ID.")
    else:
        user_id = resultado["user_id"]
        username = resultado["username"] or "Desconhecido"
        await update.message.reply_text(
            f"🔍 *Informações do pedido*\n"
            f"📽️ ID do vídeo: `{video_id}`\n"
            f"👤 User ID: `{user_id}`\n"
            f"📝 Nome de usuário: `{username}`",
            parse_mode="Markdown"
        )

    # 6) Encerra o fluxo, caso tenha sido iniciado por conversa
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
    conn = None
    try:
        conn = get_conn_pg()
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
                id SERIAL PRIMARY KEY,
                vid TEXT,
                username TEXT,
                ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )'''
        )
        cur.execute(
            '''CREATE TABLE IF NOT EXISTS pending_requests (
                id SERIAL PRIMARY KEY,
                user_id TEXT,
                username TEXT,
                video_id TEXT,
                requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'pendente'
            )'''
        )
        conn.commit()
    except Exception:
        logger.exception("Erro ao inicializar o banco de dados")
    finally:
        if conn:
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

    # Roda a contagem em thread separada
    resultado = await asyncio.to_thread(
        buscar_um_do_banco,
        "SELECT COUNT(*) AS total FROM pending_requests"
    )
    total = resultado["total"] if resultado else 0

    await update.message.reply_text(f"📊 Total de pedidos registrados no banco: {total}")

def load_admins_from_db():
    conn = get_conn_pg()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM admins")
    rows = [r[0] for r in cur.fetchall()]
    conn.close()
    return rows

def inserir_admin_db(user_id: int):
    conn = get_conn_pg()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO admins(user_id)
            VALUES (%s)
            ON CONFLICT (user_id) DO NOTHING
        """, (user_id,))
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

    # 3) checa se já é admin
    if novo_id in ADMIN_IDS:
        return await update.message.reply_text("⚠️ Esse usuário já é admin.")

    # 4) insere no DB e na lista em memória
    await asyncio.to_thread(inserir_admin_db, novo_id)
    ADMIN_IDS.append(novo_id)

    await update.message.reply_text(f"✅ Usuário `{novo_id}` adicionado como admin.", parse_mode="Markdown")


# ————— Ponto de entrada —————
if __name__ == "__main__":
    init_db()
    conn = get_conn_pg()
    if conn:
        print("Conexão com o banco de dados bem-sucedida!")
        conn.close()
    else:
        print("Falha na conexão com o banco de dados.")

    dynamic_admins = load_admins_from_db()
    # mescla com os admins fixos que você colocava manualmente
    ADMIN_IDS = list(set(ADMIN_IDS + dynamic_admins))

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(configurar_mural)
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