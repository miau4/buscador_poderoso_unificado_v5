"""Entrada única: um dado entra, vários módulos rodam EM PARALELO e saem num relatório só.

relatorio(tipo, valor, client, db) -> (blocos, status)
  - blocos: lista de textos (cada um é uma seção "■ Título ...")
  - status: "ok", "cache", "nao_encontrado", "cnpj_invalido", "fonte_indisponivel"...

Cada seção é independente: se uma fonte cair, as outras continuam e a seção que falhou
aparece com o motivo, em vez de derrubar o relatório inteiro.
"""
import asyncio
import hashlib
import json
import logging
import os
import re
from urllib.parse import quote, urlparse

import httpx

import cnpj as cnpj_mod
import modulos as mod
from entrada import cpf_valido, regiao_fiscal, telefone_normalizado

CACHE_TTL = 24 * 3600
TEMPO_SECAO = 30  # segundos máximos por fonte

PROVEDORES_GRATUITOS = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com", "yahoo.com",
    "yahoo.com.br", "icloud.com", "me.com", "proton.me", "protonmail.com", "bol.com.br", "uol.com.br",
    "terra.com.br", "ig.com.br", "globo.com", "globomail.com", "zipmail.com.br", "aol.com",
}
DESCARTAVEIS = {
    "mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com", "temp-mail.org", "yopmail.com",
    "trashmail.com", "sharklasers.com", "getnada.com", "dispostable.com", "maildrop.cc", "throwawaymail.com",
    "fakeinbox.com", "mohmal.com", "emailondeck.com",
}
SLD = {"com", "net", "org", "gov", "edu", "mil", "art", "blog", "eco", "adv", "app", "dev", "ind", "inf", "tec"}


# ======================================================================= UTILIDADES
def _host(valor: str) -> str:
    v = valor.strip()
    if "://" in v:
        v = urlparse(v).hostname or v
    return v.split("/")[0].split(":")[0].lower().rstrip(".")


def dominio_registravel(host: str) -> str:
    """exemplo: www.loja.com.br -> loja.com.br ; blog.site.com -> site.com"""
    p = host.split(".")
    if len(p) >= 3 and len(p[-1]) == 2 and p[-2] in SLD:
        return ".".join(p[-3:])
    return ".".join(p[-2:]) if len(p) >= 2 else host


def _reais(v) -> str:
    try:
        return "R$ " + f"{float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return "-"


def _mapa(consulta: str) -> str:
    return "https://www.google.com/maps/search/?api=1&query=" + quote(consulta)


async def _seguro(coro, padrao=None):
    """Roda uma consulta auxiliar; se falhar, devolve o padrão (não derruba a seção)."""
    try:
        return await coro
    except Exception:
        return padrao


async def _rodar(secoes: list) -> list[str]:
    """secoes: [(titulo, coroutine)]. Roda tudo junto e devolve os blocos de texto prontos."""
    async def um(titulo, coro):
        try:
            txt = await asyncio.wait_for(coro, TEMPO_SECAO)
        except mod.NaoEncontrado:
            txt = "Não encontrado nas fontes consultadas."
        except asyncio.TimeoutError:
            txt = "A fonte demorou demais (tempo esgotado)."
        except httpx.HTTPError:
            txt = "Fonte indisponível no momento."
        except Exception:
            logging.exception("Erro na seção %s", titulo)
            txt = "Erro ao consultar esta fonte (detalhe no terminal)."
        return f"■ {titulo}\n{txt}" if txt else None

    res = await asyncio.gather(*(um(t, c) for t, c in secoes))
    return [r for r in res if r]


# ======================================================================= SEÇÕES
async def sec_cep(client, cep: str) -> str:
    d = await mod._get_json(client, f"https://brasilapi.com.br/api/cep/v2/{cep}")
    via = await _seguro(mod._get_json(client, f"https://viacep.com.br/ws/{cep}/json/"))
    if isinstance(via, dict) and via.get("erro"):
        via = None
    cidade = " - ".join(p for p in [d.get("city"), d.get("state")] if p)
    linhas = [
        f"CEP: {cep[:5]}-{cep[5:]}",
        f"Logradouro: {d.get('street') or '-'}",
        f"Bairro: {d.get('neighborhood') or '-'}",
        f"Cidade: {cidade or '-'}",
    ]
    if via:
        linhas.append(f"DDD: {via.get('ddd') or '-'}   Código IBGE: {via.get('ibge') or '-'}")
    coords = ((d.get("location") or {}).get("coordinates")) or {}
    if coords.get("latitude") and coords.get("longitude"):
        linhas.append(f"Mapa: {_mapa(str(coords['latitude']) + ',' + str(coords['longitude']))}")
    else:
        linhas.append("Mapa: " + _mapa(f"{d.get('street') or ''} {d.get('neighborhood') or ''} {d.get('city') or ''} {d.get('state') or ''} Brasil"))
    return "\n".join(linhas)


async def _rdap(client, reg: str):
    return await mod._get_json(client, f"https://rdap.org/domain/{reg}")


def _resumo_rdap(d: dict) -> list[str]:
    eventos = {e.get("eventAction"): (e.get("eventDate") or "")[:10] for e in d.get("events", [])}
    registrar = None
    for ent in d.get("entities", []):
        if "registrar" in (ent.get("roles") or []):
            vcard = (ent.get("vcardArray") or [None, []])[1]
            registrar = next((v[3] for v in vcard if v and v[0] == "fn"), None)
    linhas = []
    if registrar:
        linhas.append(f"Registrador: {registrar}")
    if eventos.get("registration"):
        linhas.append(f"Registrado em: {eventos['registration']}")
    if eventos.get("expiration"):
        linhas.append(f"Expira em: {eventos['expiration']}")
    if eventos.get("last changed"):
        linhas.append(f"Última alteração: {eventos['last changed']}")
    if d.get("status"):
        linhas.append("Status: " + ", ".join(d["status"][:4]))
    return linhas


async def _http_site(client, host: str):
    for esquema in ("https", "http"):
        try:
            r = await client.get(f"{esquema}://{host}", headers=mod.UA, timeout=8, follow_redirects=True)
            titulo = re.search(r"<title[^>]*>(.*?)</title>", r.text[:50000], re.I | re.S)
            return {
                "status": r.status_code,
                "url": str(r.url),
                "servidor": r.headers.get("server"),
                "titulo": re.sub(r"\s+", " ", titulo.group(1)).strip()[:100] if titulo else None,
            }
        except Exception:
            continue
    return None


async def sec_dominio(client, valor: str) -> str:
    host = _host(valor)
    reg = dominio_registravel(host)
    # return_exceptions=True: uma fonte que falha não derruba as outras. Se TODAS as consultas de DNS
    # falharem por erro (e não por "não existe"), avisamos "fonte indisponível" em vez de "não encontrado".
    resultados = await asyncio.gather(
        _rdap(client, reg),
        mod._dns(client, host, "A"),
        mod._dns(client, host, "AAAA"),
        mod._dns(client, reg, "MX"),
        mod._dns(client, reg, "NS"),
        mod._dns(client, reg, "TXT"),
        mod._dns(client, f"_dmarc.{reg}", "TXT"),
        _http_site(client, host),
        return_exceptions=True,
    )
    dns_res = resultados[1:7]
    if all(isinstance(r, Exception) for r in dns_res):
        raise next(r for r in dns_res if isinstance(r, Exception))
    rdap = None if isinstance(resultados[0], Exception) else resultados[0]
    a, aaaa, mx, ns, txt, dmarc = [[] if isinstance(r, Exception) else r for r in dns_res]
    site = None if isinstance(resultados[7], Exception) else resultados[7]
    if not rdap and not a and not aaaa and not mx and not ns:
        raise mod.NaoEncontrado(host)

    linhas = [f"Domínio: {host}" + (f"  (registro: {reg})" if reg != host else "")]
    if rdap:
        linhas += _resumo_rdap(rdap)
    if a or aaaa:
        linhas.append("IPs: " + ", ".join((a + aaaa)[:4]))
    if a:
        ip = await _seguro(mod._get_json(client, f"https://rdap.org/ip/{a[0]}"))
        if isinstance(ip, dict):
            linhas.append(f"Hospedagem (IP {a[0]}): {ip.get('name') or '-'} / {ip.get('country') or '-'}")
    if ns:
        linhas.append("Servidores de nome: " + ", ".join(ns[:4]))
    linhas.append("Recebe e-mail (MX): " + (", ".join(m.split()[-1] for m in mx[:3]) if mx else "não"))
    spf = next((t for t in txt if t.lower().startswith("v=spf1")), None)
    dm = next((t for t in dmarc if t.upper().startswith("V=DMARC1")), None)
    politica = re.search(r"p=(\w+)", dm, re.I) if dm else None
    linhas.append(f"SPF: {'configurado' if spf else 'ausente'}   DMARC: " +
                  (f"configurado (política {politica.group(1)})" if politica else "ausente"))
    if site:
        linhas.append(f"Site: HTTP {site['status']} -> {site['url']}")
        if site["titulo"]:
            linhas.append(f"Título: {site['titulo']}")
        if site["servidor"]:
            linhas.append(f"Servidor web: {site['servidor']}")
    else:
        linhas.append("Site: não respondeu por HTTP/HTTPS")
    return "\n".join(linhas)


RE_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


async def _gravatar(client, email: str) -> list[str]:
    """Perfil público do Gravatar (nome, local, contas verificadas, links). Vazio se não existir."""
    h = hashlib.md5(email.encode()).hexdigest()
    r = await client.get(f"https://www.gravatar.com/{h}.json", headers=mod.UA, timeout=6, follow_redirects=True)
    if r.status_code != 200:
        return []
    e = (r.json().get("entry") or [{}])[0]
    linhas = [f"Gravatar: {e.get('displayName') or e.get('preferredUsername') or 'perfil ativo'}"]
    if e.get("preferredUsername"):
        linhas.append(f"  Usuário: {e['preferredUsername']}  ({e.get('profileUrl') or '-'})")
    if e.get("currentLocation"):
        linhas.append(f"  Local: {e['currentLocation']}")
    for c in (e.get("accounts") or [])[:8]:
        linhas.append(f"  Conta vinculada: {c.get('name') or c.get('shortname')}: {c.get('url') or '-'}")
    for u in (e.get("urls") or [])[:4]:
        linhas.append(f"  Link: {u.get('value') or '-'}")
    return linhas


async def _vazamentos(client, email: str) -> list[str]:
    """Vazamentos conhecidos. HIBP (com HIBP_API_KEY no .env) ou XposedOrNot (gratuito, sem chave)."""
    chave = os.getenv("HIBP_API_KEY", "").strip()
    if chave:
        r = await client.get(f"https://haveibeenpwned.com/api/v3/breachedaccount/{quote(email)}",
                             params={"truncateResponse": "false"},
                             headers={"hibp-api-key": chave, "user-agent": "buscador-bot"}, timeout=10)
        if r.status_code == 404:
            return ["Vazamentos (HIBP): nenhum registrado."]
        if r.status_code in (401, 403):
            return ["Vazamentos (HIBP): chave inválida ou sem permissão (HIBP_API_KEY)."]
        if r.status_code == 429:
            return ["Vazamentos (HIBP): limite de consultas atingido, tente em instantes."]
        r.raise_for_status()
        itens = r.json()
        linhas = [f"Vazamentos (HIBP): {len(itens)} encontrado(s)"]
        for b in itens[:15]:
            classes = ", ".join((b.get("DataClasses") or [])[:4])
            linhas.append(f"  • {b.get('Name')} ({(b.get('BreachDate') or '?')[:4]}): {classes}")
        if len(itens) > 15:
            linhas.append(f"  ... e mais {len(itens) - 15}")
        return linhas

    r = await client.get(f"https://api.xposedornot.com/v1/check-email/{quote(email)}", headers=mod.UA, timeout=10)
    if r.status_code == 404:
        return ["Vazamentos (XposedOrNot): nenhum registrado."]
    if r.status_code == 429:
        return ["Vazamentos (XposedOrNot): limite de consultas atingido, tente em instantes."]
    r.raise_for_status()
    nomes = [n for grupo in (r.json().get("breaches") or []) for n in (grupo if isinstance(grupo, list) else [grupo])]
    if not nomes:
        return ["Vazamentos (XposedOrNot): nenhum registrado."]
    linhas = [f"Vazamentos (XposedOrNot): {len(nomes)} encontrado(s)"]
    linhas += [f"  • {n}" for n in nomes[:20]]
    if len(nomes) > 20:
        linhas.append(f"  ... e mais {len(nomes) - 20}")
    linhas.append("  (Cada vazamento indica um serviço onde existe/existiu conta com este e-mail.)")
    return linhas


async def _github_por_email(client, email: str) -> list[str]:
    """Contas do GitHub que expõem este e-mail publicamente."""
    r = await client.get("https://api.github.com/search/users", params={"q": f"{email} in:email"},
                         headers=mod.UA, timeout=8)
    if r.status_code in (403, 429):
        return ["GitHub: limite da API atingido."]
    r.raise_for_status()
    itens = r.json().get("items") or []
    if not itens:
        return []
    return ["GitHub (e-mail público no perfil):"] + [f"  • {i.get('login')}: {i.get('html_url')}" for i in itens[:3]]


async def sec_email(client, email: str) -> str:
    email = email.strip().lower()
    if not RE_EMAIL.fullmatch(email):
        return "E-mail inválido."
    usuario, _, dominio = email.partition("@")
    if dominio in DESCARTAVEIS:
        tipo = "Descartável (e-mail temporário)"
    elif dominio in PROVEDORES_GRATUITOS:
        tipo = "Provedor gratuito"
    else:
        tipo = "Domínio próprio/corporativo"

    mx, grav, vaz, gh = await asyncio.gather(
        _seguro(mod._dns(client, dominio, "MX"), []),
        _seguro(_gravatar(client, email), None),
        _seguro(_vazamentos(client, email), None),
        _seguro(_github_por_email(client, email), None),
    )
    linhas = [
        f"E-mail: {email}",
        f"Provedor: {tipo}",
        f"Servidor de e-mail (MX): {'ativo' if mx else 'INATIVO (provavelmente não recebe e-mail)'}",
        "",
    ]
    linhas += grav if grav else (["Gravatar: sem perfil público."] if grav == [] else ["Gravatar: fonte indisponível."])
    linhas.append("")
    linhas += vaz if vaz else ["Vazamentos: fonte indisponível agora."]
    if gh:
        linhas += ["", *gh]

    # Perfis com o mesmo @ do e-mail (só vale se a pessoa usa o mesmo nome em todo lugar)
    handle = re.sub(r"\+.*$", "", usuario)  # tira o "+tag" do Gmail
    if mod.RE_USUARIO.fullmatch(handle) and dominio not in DESCARTAVEIS:
        achados, _ = await mod.varrer_usuario(client, handle)
        linhas += ["", f"Perfis com o usuário \"{handle}\" (palpite, confirme abrindo):"]
        linhas += [f"  • {a}" for a in achados] if achados else ["  nenhum encontrado nos sites verificáveis."]

    q = quote(f'"{email}"')
    linhas += ["", "Busca manual (rápida, na web aberta):",
               f"  • Google: https://www.google.com/search?q={q}",
               f"  • DuckDuckGo: https://duckduckgo.com/?q={q}"]
    return "\n".join(linhas)


async def sec_telefone(client, d: str) -> str:
    ddd, numero = d[:2], d[2:]
    tipo = "Celular" if len(d) == 11 else "Fixo"
    fmt = f"({ddd}) {numero[:-4]}-{numero[-4:]}"
    info = await _seguro(mod._get_json(client, f"https://brasilapi.com.br/api/ddd/v1/{ddd}"))
    linhas = [f"Telefone: {fmt}  (+55 {ddd} {numero})", f"Tipo: {tipo}"]
    if isinstance(info, dict):
        cidades = info.get("cities") or []
        extra = f" (+{len(cidades) - 6} outras)" if len(cidades) > 6 else ""
        linhas.append(f"DDD {ddd}: {info.get('state') or '-'} - " + ", ".join(c.title() for c in cidades[:6]) + extra)
    linhas.append("Operadora: não é possível saber só pelo número (portabilidade permite trocar de operadora).")
    return "\n".join(linhas)


def sec_cpf(digitos: str) -> str:
    if not cpf_valido(digitos):
        return "CPF inválido: os dígitos verificadores não conferem."
    f = f"{digitos[:3]}.{digitos[3:6]}.{digitos[6:9]}-{digitos[9:]}"
    return (f"CPF {f}: válido (dígitos verificadores conferem).\n"
            f"Região fiscal de emissão: {regiao_fiscal(digitos)}\n"
            "Obs.: o bot não consulta dados cadastrais de CPF.")


# ---------------------------------------------------------------- PLACA
ABC = "ABCDEFGHIJ"


def analisar_placa(placa: str) -> dict:
    p = re.sub(r"[^0-9A-Za-z]", "", placa).upper()
    if re.fullmatch(r"[A-Z]{3}\d{4}", p):
        equivalente = p[:4] + ABC[int(p[4])] + p[5:]
        return {"placa": p, "formato": "Antigo (3 letras + 4 números)", "equivalente": f"Mercosul: {equivalente}"}
    if re.fullmatch(r"[A-Z]{3}\d[A-Z]\d{2}", p):
        equivalente = p[:4] + str(ABC.index(p[4]) if p[4] in ABC else "?") + p[5:]
        eq = f"Antigo: {equivalente[:3]}-{equivalente[3:]}" if "?" not in equivalente else "sem equivalente no formato antigo"
        return {"placa": p, "formato": "Mercosul", "equivalente": eq}
    return {"placa": p, "formato": "formato desconhecido", "equivalente": None}


CAMPOS_PLACA = [
    ("Marca", ("marca", "brand")), ("Modelo", ("modelo", "model")), ("Ano de fabricação", ("ano", "anoFabricacao", "year")),
    ("Ano do modelo", ("anoModelo", "ano_modelo")), ("Cor", ("cor", "color")), ("Combustível", ("combustivel",)),
    ("Município", ("municipio", "cidade")), ("UF", ("uf", "estado")), ("Situação", ("situacao",)),
    ("Chassi", ("chassi",)),
]


def _json_env(nome: str) -> dict:
    """Lê um JSON do .env (ex.: PLACA_API_BODY). Vazio ou inválido = {}."""
    bruto = os.getenv(nome, "").strip()
    if not bruto:
        return {}
    try:
        d = json.loads(bruto)
        return d if isinstance(d, dict) else {}
    except ValueError:
        logging.warning("%s no .env não é um JSON válido; ignorado.", nome)
        return {}


async def _placa_provedor(client, placa: str):
    """Provedor de dados da placa, configurável SEM mexer no código.

    No .env:
      PLACA_API_URL=https://seu-provedor/api/placa/{placa}   ({placa} é trocado pela placa)
      PLACA_API_METHOD=GET                                   (GET ou POST; padrão GET)
      PLACA_API_KEY=sua_chave                                (opcional)
      PLACA_API_KEY_HEADER=Authorization                     (nome do cabeçalho; padrão Authorization)
      PLACA_API_KEY_PREFIX=Bearer                            (padrão Bearer; deixe vazio p/ enviar só a chave)
      PLACA_API_BODY={"placa": "{placa}"}                    (só POST: corpo JSON, {placa} é trocado)
      PLACA_API_HEADERS={"x-outro": "valor"}                 (opcional: cabeçalhos extras em JSON)
    """
    url = os.getenv("PLACA_API_URL", "").strip()
    if not url:
        return None
    metodo = os.getenv("PLACA_API_METHOD", "GET").strip().upper()
    if metodo not in ("GET", "POST"):
        raise ValueError("PLACA_API_METHOD deve ser GET ou POST")

    headers = {k: str(v) for k, v in _json_env("PLACA_API_HEADERS").items()}
    chave = os.getenv("PLACA_API_KEY", "").strip()
    if chave:
        cab = os.getenv("PLACA_API_KEY_HEADER", "Authorization").strip() or "Authorization"
        prefixo = os.getenv("PLACA_API_KEY_PREFIX", "Bearer").strip()
        headers[cab] = f"{prefixo} {chave}" if prefixo else chave

    kwargs = {"headers": headers, "timeout": mod.TIMEOUT, "follow_redirects": True}
    if metodo == "POST":
        corpo = os.getenv("PLACA_API_BODY", "").strip() or '{"placa": "{placa}"}'
        try:
            kwargs["json"] = json.loads(corpo.replace("{placa}", placa))
        except ValueError:
            raise ValueError("PLACA_API_BODY no .env não é um JSON válido")
    elif "{placa}" not in url:
        raise ValueError("No modo GET, PLACA_API_URL precisa conter {placa}")

    resp = await client.request(metodo, url.replace("{placa}", placa), **kwargs)
    if resp.status_code == 404:
        raise mod.NaoEncontrado(placa)
    resp.raise_for_status()
    d = resp.json()

    if isinstance(d, list) and d:
        d = d[0]
    # Desce por envelopes comuns: {"dados": {...}}, {"data": {...}}, {"resultado": {...}}...
    for _ in range(3):
        if isinstance(d, dict):
            interno = next((d[k] for k in ("dados", "data", "resultado", "result", "veiculo") if isinstance(d.get(k), dict)), None)
            if interno is None:
                break
            d = interno
    if not isinstance(d, dict):
        return None
    linhas = []
    for rotulo, chaves in CAMPOS_PLACA:
        v = next((d[k] for k in chaves if d.get(k) not in (None, "")), None)
        if v is not None:
            linhas.append(f"{rotulo}: {v}")
    return linhas or None


async def sec_placa(client, valor: str) -> str:
    a = analisar_placa(valor)
    linhas = [f"Placa: {a['placa']}", f"Formato: {a['formato']}"]
    if a["equivalente"]:
        linhas.append(f"Equivalente: {a['equivalente']}")
    dados = None
    configurado = bool(os.getenv("PLACA_API_URL", "").strip())
    if configurado and a["equivalente"] is None and a["formato"] == "formato desconhecido":
        linhas.append("Placa fora do padrão antigo/Mercosul: provedor não consultado.")
    elif configurado:
        try:
            dados = await _placa_provedor(client, a["placa"])
        except mod.NaoEncontrado:
            linhas.append("Provedor: placa não encontrada.")
        except httpx.HTTPStatusError as e:
            codigo = e.response.status_code
            dica = {401: "chave inválida ou ausente", 403: "acesso negado (chave/plano/captcha)",
                    429: "limite de consultas atingido"}.get(codigo, "erro do provedor")
            logging.error("Provedor de placa respondeu HTTP %s", codigo)
            linhas.append(f"Provedor de placa falhou: HTTP {codigo} ({dica}).")
        except Exception:
            logging.exception("Provedor de placa falhou")
            linhas.append("Provedor de placa configurado, mas falhou (veja o terminal).")
    if dados:
        linhas += ["", "Dados do provedor:"] + dados
    elif not configurado:
        linhas.append("Dados do veículo: nenhum provedor configurado (PLACA_API_URL no .env).")
    linhas += [
        "",
        "Consultas oficiais (gratuitas, exigem login gov.br ou o Renavam):",
        "• Senatran, dados da placa: https://www.gov.br/pt-br/servicos/consultar-online-os-dados-de-placa-veicular",
        "• Débitos, multas e IPVA: site do Detran do estado do veículo",
        "• Seus próprios veículos: app Carteira Digital de Trânsito",
        "Para valor de mercado, envie o código FIPE do modelo.",
    ]
    return "\n".join(linhas)


# ---------------------------------------------------------------- FIPE
def normalizar_fipe(texto: str) -> str:
    d = re.sub(r"\D", "", texto)
    return f"{d[:6]}-{d[6:]}" if len(d) == 7 else d


async def sec_fipe(client, codigo: str) -> str:
    dados = await mod._get_json(client, f"https://brasilapi.com.br/api/fipe/preco/v1/{codigo}")
    if not dados:
        raise mod.NaoEncontrado(codigo)
    p = dados[0]
    linhas = [f"Código FIPE: {codigo}", f"Marca: {p.get('marca') or '-'}", f"Modelo: {p.get('modelo') or '-'}", "", "Valores por ano/combustível:"]
    for v in dados[:6]:
        linhas.append(f"• {v.get('anoModelo') or '-'} {v.get('combustivel') or ''}: {v.get('valor') or '-'} ({v.get('mesReferencia') or '-'})".replace("  ", " "))
    if len(dados) > 6:
        linhas.append(f"... e mais {len(dados) - 6} versões")
    return "\n".join(linhas)


# ======================================================================= CNPJ
def _extras_cnpj(d: dict) -> str:
    linhas = []
    if d.get("porte"):
        linhas.append(f"Porte: {d['porte']}")
    if d.get("natureza_juridica"):
        linhas.append(f"Natureza jurídica: {d['natureza_juridica']}")
    if d.get("capital_social") is not None:
        linhas.append(f"Capital social: {_reais(d['capital_social'])}")
    if d.get("opcao_pelo_simples") is not None:
        linhas.append("Simples Nacional: " + ("sim" if d["opcao_pelo_simples"] else "não") +
                      "   MEI: " + ("sim" if d.get("opcao_pelo_mei") else "não"))
    if d.get("data_situacao_cadastral"):
        linhas.append(f"Situação desde: {d['data_situacao_cadastral']}")
    sec = d.get("cnaes_secundarios") or []
    if sec:
        linhas.append(f"Atividades secundárias: {len(sec)} (ex.: {sec[0].get('descricao') or '-'})")
    tels = [d.get(k) for k in ("ddd_telefone_1", "ddd_telefone_2") if d.get(k)]
    if tels:
        linhas.append("Telefones cadastrados: " + ", ".join(tels))
    if d.get("email"):
        linhas.append(f"E-mail cadastrado: {str(d['email']).lower()}")
    return "\n".join(linhas)


async def relatorio_cnpj(bruto: str, client, db):
    cnpj = cnpj_mod.normalize(bruto)
    if not cnpj_mod.is_valid(cnpj):
        return ["CNPJ inválido. Confira os números e tente de novo."], "cnpj_invalido"
    status = "cache"
    d = db.cache_get(f"cnpj:{cnpj}")
    if d is None:
        try:
            d = await cnpj_mod.fetch(cnpj, client)
        except cnpj_mod.CnpjNotFound:
            return ["CNPJ não encontrado na base consultada."], "nao_encontrado"
        except httpx.HTTPError as exc:
            logging.warning("Falha ao consultar a BrasilAPI: %r", exc)
            return ["A fonte de dados está indisponível. Tente em alguns minutos."], "fonte_indisponivel"
        db.cache_set(f"cnpj:{cnpj}", d, CACHE_TTL)
        status = "ok"

    blocos = ["■ Empresa\n" + cnpj_mod.format_company(cnpj, d)]
    extras = _extras_cnpj(d)
    if extras:
        blocos.append("■ Cadastro\n" + extras)

    secoes = []
    cep = re.sub(r"\D", "", d.get("cep") or "")
    if len(cep) == 8:
        secoes.append(("Endereço (CEP)", sec_cep(client, cep)))
    email = str(d.get("email") or "").lower()
    if "@" in email:
        dom = email.rpartition("@")[2]
        if dom not in PROVEDORES_GRATUITOS and dom not in DESCARTAVEIS:
            secoes.append((f"Domínio do e-mail da empresa ({dom})", sec_dominio(client, dom)))
    tel = telefone_normalizado(re.sub(r"\D", "", str(d.get("ddd_telefone_1") or "")))
    if tel:
        secoes.append(("Telefone principal", sec_telefone(client, tel)))
    blocos += await _rodar(secoes)
    return blocos, status


# ======================================================================= ROTEADOR DE TIPOS
async def relatorio(tipo: str, valor: str, client, db):
    """Ponto único de entrada. Devolve (blocos, status)."""
    if tipo == "cnpj":
        return await relatorio_cnpj(valor, client, db)

    if tipo == "cep":
        cep = re.sub(r"\D", "", valor)
        secoes = [("CEP", sec_cep(client, cep))]
    elif tipo == "fipe":
        secoes = [("Veículo (FIPE)", sec_fipe(client, normalizar_fipe(valor)))]
    elif tipo == "dominio":
        secoes = [("Domínio", sec_dominio(client, valor))]
    elif tipo == "email":
        secoes = [("E-mail", sec_email(client, valor))]
        dom = valor.strip().lower().rpartition("@")[2]
        if dom not in PROVEDORES_GRATUITOS and dom not in DESCARTAVEIS:
            secoes.append((f"Domínio {dom}", sec_dominio(client, dom)))
    elif tipo == "telefone":
        d = telefone_normalizado(re.sub(r"\D", "", valor))
        if not d:
            return ["Telefone inválido (confira DDD e quantidade de dígitos)."], "invalido"
        secoes = [("Telefone", sec_telefone(client, d))]
    elif tipo == "cpf":
        return ["■ CPF\n" + sec_cpf(re.sub(r"\D", "", valor))], "ok"
    elif tipo == "placa":
        secoes = [("Veículo (placa)", sec_placa(client, valor))]
    else:
        return ["Tipo de consulta não suportado."], "nao_suportado"

    blocos = await _rodar(secoes)
    nao = any("Não encontrado" in b for b in blocos[:1])
    return blocos, ("nao_encontrado" if nao and len(blocos) == 1 else "ok")
