"""Buscador Poderoso Unificado: envie um dado e receba um relatório cruzando várias fontes."""
import functools
import logging
import os
import re
import time
import traceback

import httpx
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Update
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

import inventario as inv
import modulos as mod_extras
import unificado
import utilitarios as utils
from db import Database
from entrada import analisar

load_dotenv()

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CACHE_TTL = 24 * 3600
AUDIT_RETENTION_DAYS = 90  # registros de auditoria mais antigos que isso são apagados ao iniciar
TERMS_VERSION = 2  # aumentei de 1 para 2 porque o texto do termo estava cortado: aceite de novo uma vez

# CORREÇÃO: o README prometia filtrar por ALLOWED_USER_IDS, mas o código nunca lia essa variável.
# Agora: se a lista estiver preenchida no .env, só esses IDs usam o bot. Se estiver vazia, fica aberto
# (útil durante o desenvolvimento), e o bot avisa no log.
ALLOWED_IDS = {int(x) for x in re.split(r"[,\s;]+", os.getenv("ALLOWED_USER_IDS", "")) if x.strip().isdigit()}

db = Database(os.getenv("DB_PATH", "bot.db"))

TERMS = (
    "TERMO DE USO\n\n"
    "Ao tocar em \"Aceito\", você declara que:\n"
    "1. Vai consultar apenas dados seus ou de pessoas que autorizaram a consulta;\n"
    "2. Sabe que cada consulta fica registrada na auditoria (e-mail, telefone e CPF são mascarados);\n"
    "3. É responsável pelo uso das informações obtidas."
)

# CORREÇÃO: sem parse_mode Markdown. O texto antigo tinha ** e "_" (/gerar_cpf) que podem
# quebrar o parser do Telegram. Texto simples nunca falha.
HELP = (
    "BUSCADOR PODEROSO UNIFICADO\n\n"
    "Envie direto o dado. O bot reconhece o tipo e cruza várias fontes num relatório só:\n\n"
    "• CNPJ: empresa, sócios, cadastro, endereço (CEP) e domínio/telefone da empresa\n"
    "• CEP: endereço, DDD, IBGE e mapa\n"
    "• Domínio ou site: registro, IP/hospedagem, DNS, e-mail (SPF/DMARC) e resposta do site\n"
    "• E-mail: provedor, MX, Gravatar, vazamentos conhecidos, GitHub, perfis com o mesmo @ e busca na web\n"
    "• Telefone: formato, tipo, DDD/estado (use tel: se houver dúvida)\n"
    "• CPF: valida o número e região fiscal (use cpf: se houver dúvida)\n"
    "• Placa: formato, equivalente Mercosul/antigo e links oficiais\n"
    "• FIPE: código do veículo, valores por ano\n"
    "• @usuario: varredura de perfis públicos\n"
    "• Link de imagem: pesquisa reversa\n"
    "• 'matrícula', 'inventário', 'imóvel': orientação patrimonial\n\n"
    "Comandos:\n"
    "/inventario - Checklist de inventário (bens de falecido)\n"
    "/gerar_cpf - Gera CPF válido para testes\n"
    "/gerar_cnpj - Gera CNPJ válido para testes\n"
    "/temp - Dicas de e-mails/telefones temporários\n"
    "/historico - Suas últimas consultas\n"
    "/meuid - Mostra seu ID do Telegram"
)

SEM_PREVIEW = LinkPreviewOptions(is_disabled=True)


def _name(user) -> str:
    return user.username or user.full_name


def _fmt_date(ts: int) -> str:
    return time.strftime("%d/%m/%Y %H:%M", time.localtime(ts))


def _alvo(tipo: str, valor: str) -> str:
    """Valor que vai para a auditoria. Dados pessoais são mascarados (LGPD)."""
    if tipo == "email":
        usuario, _, dominio = valor.partition("@")
        return f"{usuario[:1]}***@{dominio}"
    if tipo in ("telefone", "cpf", "cpf_ou_telefone"):
        d = re.sub(r"\D", "", valor)
        return f"***{d[-4:]}" if d else "***"
    if tipo == "rede_social":
        return f"@{valor.lstrip('@')[:2]}***"
    return valor[:120]


async def gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Roda antes de tudo (grupo -1). Bloqueia quem não está em ALLOWED_USER_IDS, quando a lista existe."""
    if not ALLOWED_IDS:
        return
    user = update.effective_user
    if user is None or user.id not in ALLOWED_IDS:
        logging.warning("Acesso bloqueado: id=%s", getattr(user, "id", None))
        if update.effective_message:
            await update.effective_message.reply_text("Acesso restrito.")
        raise ApplicationHandlerStop


async def send_terms(update: Update):
    keyboard = [[InlineKeyboardButton("Aceito", callback_data="aceitar_termos")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query and update.callback_query.message:
        await update.callback_query.message.reply_text(TERMS, reply_markup=reply_markup)
    elif update.message:
        await update.message.reply_text(TERMS, reply_markup=reply_markup)


def requires_terms(func):
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user:
            return
        if not db.has_accepted_terms(update.effective_user.id, TERMS_VERSION):
            await send_terms(update)
            return
        return await func(update, context)
    return wrapper


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not update.message:
        return
    if not db.has_accepted_terms(user.id, TERMS_VERSION):
        await send_terms(update)
    else:
        await update.message.reply_text(HELP)


async def meuid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.effective_user:
        await update.message.reply_text(f"Seu ID do Telegram é: {update.effective_user.id}")


async def aceitar_termos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.message or not update.effective_user:
        return
    await query.answer()
    user = update.effective_user
    db.accept_terms(user.id, TERMS_VERSION)
    db.log(user.id, _name(user), "aceitar_termos", None, None, f"versao_{TERMS_VERSION}")
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text("Termos aceitos.\n\n" + HELP)


async def enviar_blocos(message, blocos: list[str]):
    """Junta as seções em mensagens de até ~3800 caracteres (limite do Telegram é 4096)."""
    atual = ""
    pedacos = []
    for b in blocos:  # bloco maior que o limite é partido em linhas, sem perder conteúdo
        while len(b) > 3800:
            corte = b.rfind("\n", 0, 3800)
            corte = corte if corte > 0 else 3800
            pedacos.append(b[:corte])
            b = b[corte:].lstrip("\n")
        pedacos.append(b)
    for b in pedacos:
        if atual and len(atual) + len(b) + 2 > 3800:
            await message.reply_text(atual, link_preview_options=SEM_PREVIEW)
            atual = ""
        atual += ("\n\n" if atual else "") + b
    if atual:
        await message.reply_text(atual, link_preview_options=SEM_PREVIEW)


# Tipos que passam pela ENTRADA ÚNICA (relatório cruzando várias fontes)
TIPOS_UNIFICADOS = {"cnpj", "cep", "fipe", "dominio", "email", "telefone", "cpf", "placa"}


@requires_terms
async def buscar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text or not update.effective_user:
        return
    user = update.effective_user
    # analisar() devolve o valor já sem o prefixo ("email:a@b.com" -> "a@b.com").
    tipo, valor = analisar(update.message.text)
    alvo = _alvo(tipo, valor)
    client = context.bot_data["http"]

    def log(status: str = "ok"):
        db.log(user.id, _name(user), tipo, alvo, None, status)

    try:
        if tipo in TIPOS_UNIFICADOS:
            await update.message.reply_text("Consultando as fontes, um instante...")
            blocos, status = await unificado.relatorio(tipo, valor, client, db)
            log(status)
            await enviar_blocos(update.message, blocos)
        elif tipo == "imagem":
            log()
            await update.message.reply_text(mod_extras.links_pesquisa_imagem(valor))
        elif tipo == "rede_social":
            resposta = await mod_extras.consultar_redes_sociais(client, valor)
            log()
            await update.message.reply_text(resposta, link_preview_options=SEM_PREVIEW)
        elif tipo == "imoveis":
            log()
            await update.message.reply_text(mod_extras.orientacao_imoveis(valor) + "\n\nChecklist completo: /inventario")
        elif tipo == "cpf_ou_telefone":
            log("ambiguo")
            await update.message.reply_text(
                "Esse número pode ser um CPF ou um telefone. Reenvie com o prefixo:\n"
                "• cpf: 12345678909\n• tel: 12345678909"
            )
        elif tipo == "gerar_cpf":
            await update.message.reply_text(f"CPF válido gerado para testes:\n`{utils.gerar_cpf()}`", parse_mode="Markdown")
        elif tipo == "gerar_cnpj":
            await update.message.reply_text(f"CNPJ válido gerado para testes:\n`{utils.gerar_cnpj()}`", parse_mode="Markdown")
        elif tipo == "temp":
            await update.message.reply_text(utils.info_temporarios())
        else:
            await update.message.reply_text("Não reconheci esse dado.\n\n" + HELP)
    except mod_extras.NaoEncontrado:
        log("nao_encontrado")
        await update.message.reply_text("Registro não encontrado nas fontes consultadas.")
    except Exception:
        # O detalhe vai só para o log (terminal). O usuário vê uma mensagem curta.
        logging.error("Erro detalhado ao processar %s:\n%s", tipo, traceback.format_exc())
        log("erro_interno")
        await update.message.reply_text("Não consegui concluir essa consulta agora. Tente novamente em instantes.")


@requires_terms
async def cmd_cnpj(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    if not context.args:
        await update.message.reply_text("Uso: /cnpj 00.000.000/0001-91")
        return
    user = update.effective_user
    await update.message.reply_text("Consultando as fontes, um instante...")
    blocos, status = await unificado.relatorio("cnpj", context.args[0], context.bot_data["http"], db)
    db.log(user.id, _name(user), "cnpj", context.args[0][:20], None, status)
    await enviar_blocos(update.message, blocos)


@requires_terms
async def cmd_gerar_cpf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text(f"`{utils.gerar_cpf()}`", parse_mode="Markdown")


@requires_terms
async def cmd_gerar_cnpj(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text(f"`{utils.gerar_cnpj()}`", parse_mode="Markdown")


@requires_terms
async def cmd_temp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text(utils.info_temporarios())


@requires_terms
async def historico(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    rows = db.recent_audit(update.effective_user.id)
    if not rows:
        await update.message.reply_text("Nenhuma consulta registrada ainda.")
        return
    lines = [f"{_fmt_date(r['ts'])} - {r['action']} {r['target'] or ''} - {r['status']}" for r in rows]
    await update.message.reply_text("\n".join(lines))


# ------------------------------------------------------------ CHECKLIST DE INVENTÁRIO
@requires_terms
async def cmd_inventario(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    estado = db.inv_get(update.effective_user.id)
    await update.message.reply_text(inv.render(estado), link_preview_options=SEM_PREVIEW)


def _numero_do_item(context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        return None
    n = int(context.args[0])
    return n if inv.item_valido(n) else None


async def _marcar(update: Update, context: ContextTypes.DEFAULT_TYPE, status: str, uso: str):
    if not update.message or not update.effective_user:
        return
    n = _numero_do_item(context)
    if n is None:
        await update.message.reply_text(f"Uso: {uso}  (N de 1 a {len(inv.ITENS)})")
        return
    db.inv_set_status(update.effective_user.id, n, status)
    await update.message.reply_text(f"Item {n} marcado como {status}.")


@requires_terms
async def cmd_feito(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _marcar(update, context, inv.FEITO, "/feito N")


@requires_terms
async def cmd_pendente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _marcar(update, context, inv.PENDENTE, "/pendente N")


@requires_terms
async def cmd_nota(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    n = _numero_do_item(context)
    texto = " ".join(context.args[1:]).strip() if context.args else ""
    if n is None or not texto:
        await update.message.reply_text(f"Uso: /nota N texto  (N de 1 a {len(inv.ITENS)})")
        return
    db.inv_set_note(update.effective_user.id, n, texto[:300])
    await update.message.reply_text(f"Nota salva no item {n}.")


async def post_init(app: Application):
    app.bot_data["http"] = httpx.AsyncClient()
    apagados = db.purge_audit(AUDIT_RETENTION_DAYS)
    if apagados:
        logging.info("Auditoria: %s registros com mais de %s dias apagados.", apagados, AUDIT_RETENTION_DAYS)
    if not ALLOWED_IDS:
        logging.warning("ALLOWED_USER_IDS vazio: qualquer pessoa que achar o bot consegue usá-lo.")


async def post_shutdown(app: Application):
    http = app.bot_data.get("http")
    if http is not None:
        await http.aclose()


def main():
    if not TOKEN:
        raise SystemExit("Defina TELEGRAM_BOT_TOKEN no arquivo .env")

    app = Application.builder().token(TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()
    app.add_handler(TypeHandler(Update, gate), group=-1)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("meuid", meuid))
    app.add_handler(CommandHandler("cnpj", cmd_cnpj))
    app.add_handler(CommandHandler("historico", historico))
    app.add_handler(CommandHandler("gerar_cpf", cmd_gerar_cpf))
    app.add_handler(CommandHandler("gerar_cnpj", cmd_gerar_cnpj))
    app.add_handler(CommandHandler("temp", cmd_temp))
    app.add_handler(CommandHandler("inventario", cmd_inventario))
    app.add_handler(CommandHandler("feito", cmd_feito))
    app.add_handler(CommandHandler("pendente", cmd_pendente))
    app.add_handler(CommandHandler("nota", cmd_nota))
    app.add_handler(CallbackQueryHandler(aceitar_termos, pattern="^aceitar_termos$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, buscar))
    logging.info("Bot iniciado. Pressione Ctrl+C para parar.")
    app.run_polling()


if __name__ == "__main__":
    main()
