"""Módulos de apoio: HTTP/DNS básicos, varredura de @usuário, pesquisa reversa de imagem e imóveis.

As consultas principais (CNPJ, CEP, domínio, e-mail, telefone, placa, FIPE, CPF) ficam em
unificado.py, que cruza várias fontes num relatório só.
"""
import asyncio
import re
from urllib.parse import quote

TIMEOUT = 10
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


class NaoEncontrado(Exception):
    pass


async def _get_json(client, url: str, **kwargs):
    resp = await client.get(url, timeout=TIMEOUT, follow_redirects=True, **kwargs)
    if resp.status_code == 404:
        raise NaoEncontrado(url)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------- DNS (via dns.google, DoH)
_TIPOS_DNS = {"A": 1, "NS": 2, "MX": 15, "TXT": 16, "AAAA": 28}


async def _dns(client, nome: str, tipo: str) -> list[str]:
    d = await _get_json(client, "https://dns.google/resolve", params={"name": nome, "type": tipo})
    codigo = _TIPOS_DNS[tipo]
    dados = [r["data"] for r in d.get("Answer", []) if r.get("type") == codigo and r.get("data")]
    if tipo == "TXT":
        dados = [x.strip('"') for x in dados]
    return dados


# ---------------------------------------------------------------- VARREDURA DE @USUÁRIO
# Só entram sites que respondem de forma confiável: 404 real (ou JSON vazio) quando o perfil não existe.
# "ausente" = texto que, se aparecer na página, indica perfil inexistente (sites que devolvem 200 sempre).
# Instagram, X e TikTok exigem login/JS e devolvem 200 para qualquer nome: ficam em LINKS_MANUAIS.
PLATAFORMAS_OSINT = [
    {"nome": "GitHub", "url": "https://api.github.com/users/{u}", "pub": "https://github.com/{u}"},
    {"nome": "GitLab", "url": "https://gitlab.com/api/v4/users?username={u}", "pub": "https://gitlab.com/{u}", "json_lista": True},
    {"nome": "Reddit", "url": "https://www.reddit.com/user/{u}/about.json", "pub": "https://www.reddit.com/user/{u}"},
    {"nome": "Keybase", "url": "https://keybase.io/_/api/1.0/user/lookup.json?usernames={u}", "pub": "https://keybase.io/{u}", "json_keybase": True},
    {"nome": "Dev.to", "url": "https://dev.to/api/users/by_username?url={u}", "pub": "https://dev.to/{u}"},
    {"nome": "Docker Hub", "url": "https://hub.docker.com/v2/users/{u}/", "pub": "https://hub.docker.com/u/{u}"},
    {"nome": "Chess.com", "url": "https://api.chess.com/pub/player/{u}", "pub": "https://www.chess.com/member/{u}"},
    {"nome": "Lichess", "url": "https://lichess.org/api/user/{u}", "pub": "https://lichess.org/@/{u}"},
    {"nome": "Hacker News", "url": "https://hacker-news.firebaseio.com/v0/user/{u}.json", "pub": "https://news.ycombinator.com/user?id={u}", "json_nao_nulo": True},
    {"nome": "Steam", "url": "https://steamcommunity.com/id/{u}", "ausente": "The specified profile could not be found"},
    {"nome": "Medium", "url": "https://medium.com/@{u}"},
    {"nome": "Behance", "url": "https://www.behance.net/{u}"},
    {"nome": "Dribbble", "url": "https://dribbble.com/{u}"},
    {"nome": "Vimeo", "url": "https://vimeo.com/{u}"},
    {"nome": "SoundCloud", "url": "https://soundcloud.com/{u}"},
    {"nome": "Pinterest", "url": "https://www.pinterest.com/{u}/"},
    {"nome": "Kaggle", "url": "https://www.kaggle.com/{u}"},
    {"nome": "Telegram", "url": "https://t.me/{u}", "presente": "tgme_page_photo"},
]

LINKS_MANUAIS = [
    ("Instagram", "https://www.instagram.com/{u}/"),
    ("X (Twitter)", "https://x.com/{u}"),
    ("TikTok", "https://www.tiktok.com/@{u}"),
    ("Facebook", "https://www.facebook.com/{u}"),
    ("LinkedIn", "https://www.linkedin.com/in/{u}"),
]

RE_USUARIO = re.compile(r"[A-Za-z0-9._-]{2,40}")


async def _checar_site(client, plataforma, usuario):
    """Devolve ('achou'|'nao'|'incerto', texto). Nunca engole erro em silêncio."""
    url = plataforma["url"].format(u=usuario)
    link = plataforma.get("pub", plataforma["url"]).format(u=usuario)
    try:
        resp = await client.get(url, headers=UA, timeout=8, follow_redirects=True)
    except Exception:
        return "incerto", f"{plataforma['nome']} (sem resposta)"
    cod = resp.status_code
    if cod in (403, 429, 999) or cod >= 500:
        return "incerto", f"{plataforma['nome']} (bloqueou a consulta, HTTP {cod})"
    if cod == 404 or cod == 410:
        return "nao", ""
    if cod != 200:
        return "incerto", f"{plataforma['nome']} (HTTP {cod})"
    try:
        if plataforma.get("json_lista"):
            return ("achou", f"{plataforma['nome']}: {link}") if resp.json() else ("nao", "")
        if plataforma.get("json_keybase"):
            them = resp.json().get("them") or []
            return ("achou", f"{plataforma['nome']}: {link}") if them and them[0] else ("nao", "")
        if plataforma.get("json_nao_nulo"):
            return ("achou", f"{plataforma['nome']}: {link}") if resp.json() else ("nao", "")
    except ValueError:
        return "incerto", f"{plataforma['nome']} (resposta inesperada)"
    if plataforma.get("ausente") and plataforma["ausente"] in resp.text:
        return "nao", ""
    if plataforma.get("presente") and plataforma["presente"] not in resp.text:
        return "nao", ""
    return "achou", f"{plataforma['nome']}: {link}"


async def varrer_usuario(client, usuario: str):
    """Devolve (achados, incertos) para o handle informado."""
    res = await asyncio.gather(*[_checar_site(client, p, usuario) for p in PLATAFORMAS_OSINT])
    achados = [t for estado, t in res if estado == "achou"]
    incertos = [t for estado, t in res if estado == "incerto"]
    return achados, incertos


async def consultar_redes_sociais(client, username: str) -> str:
    usuario = username.strip().lstrip("@")
    if not RE_USUARIO.fullmatch(usuario):
        return "Usuário inválido. Use de 2 a 40 caracteres: letras, números, ponto, hífen ou underline."
    achados, incertos = await varrer_usuario(client, usuario)

    linhas = [f"Varredura para @{usuario} ({len(PLATAFORMAS_OSINT)} sites verificáveis)", ""]
    if achados:
        linhas += ["Perfis encontrados (confirme que são da mesma pessoa):"] + [f"• {a}" for a in achados]
    else:
        linhas.append("Nenhum perfil encontrado nos sites verificáveis.")
    if incertos:
        linhas += ["", "Inconclusivo (o site bloqueou ou não respondeu):"] + [f"• {i}" for i in incertos]
    linhas += ["", "Sites que não dá para checar automaticamente (abra e veja):"]
    linhas += [f"• {n}: {u.format(u=usuario)}" for n, u in LINKS_MANUAIS]
    linhas.append("\nObs.: achar o mesmo @ em outro site não prova que é a mesma pessoa.")
    return "\n".join(linhas)


# ---------------------------------------------------------------- IMAGENS E IMÓVEIS
def links_pesquisa_imagem(url: str) -> str:
    u = quote(url, safe="")
    return f"Pesquisa reversa de imagem:\nGoogle Lens: https://lens.google.com/uploadbyurl?url={u}\nTinEye: https://tineye.com/search?url={u}"


def orientacao_imoveis(termo: str) -> str:
    return (
        f"CONSULTA PATRIMONIAL DE IMÓVEIS:\n\nTermo: {termo}\n\n"
        "Para buscas patrimoniais oficiais por CPF/Nome para fins de inventário, utilize:\n"
        "1. RI Digital (ridigital.org.br) - Pesquisa Nacional de Bens.\n"
        "2. Cartórios de Registro de Imóveis da comarca de residência."
    )
