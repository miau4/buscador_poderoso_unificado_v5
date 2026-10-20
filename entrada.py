"""Reconhece o tipo de dado que o atendente digitou.

Uso principal: analisar(texto) -> (tipo, valor_limpo)
  - tipo: "cnpj", "cpf", "telefone", "cpf_ou_telefone", "cep", "fipe", "placa",
          "email", "dominio", "imagem", "rede_social", "imoveis", ...
  - valor_limpo: o dado sem o prefixo (ex.: "email:a@b.com" -> "a@b.com").

detectar(texto) continua existindo e devolve só o tipo.
"""
import re

# DDDs válidos no Brasil (evita tratar qualquer número de 10/11 dígitos como telefone).
DDDS = {
    11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 24, 27, 28, 31, 32, 33, 34, 35, 37, 38,
    41, 42, 43, 44, 45, 46, 47, 48, 49, 51, 53, 54, 55, 61, 62, 63, 64, 65, 66, 67, 68, 69,
    71, 73, 74, 75, 77, 79, 81, 82, 83, 84, 85, 86, 87, 88, 89, 91, 92, 93, 94, 95, 96, 97, 98, 99,
}

# Prefixo digitado pelo usuário -> tipo forçado (resolve ambiguidades, ex.: "cpf:" / "tel:").
PREFIXOS = {
    "cep:": "cep",
    "cnpj:": "cnpj",
    "cpf:": "cpf",
    "fipe:": "fipe",
    "dominio:": "dominio",
    "site:": "dominio",
    "email:": "email",
    "e-mail:": "email",
    "placa:": "placa",
    "veiculo:": "placa",
    "tel:": "telefone",
    "telefone:": "telefone",
    "user:": "rede_social",
}

# Palavras inteiras (\b) que indicam busca patrimonial/inventário.
# Antes, "bens" dentro de "joao_bens@gmail.com" ou "bensonhurst.com" caía aqui por engano.
RE_IMOVEIS = re.compile(
    r"\b(imovel|imóvel|imoveis|imóveis|terreno|matricula|matrícula|cartorio|cartório|inventario|inventário|bens)\b",
    re.IGNORECASE,
)

# Região fiscal do CPF (9º dígito) -> estados.
REGIAO_FISCAL = {
    0: "RS", 1: "DF, GO, MS, MT, TO", 2: "AC, AM, AP, PA, RO, RR", 3: "CE, MA, PI",
    4: "AL, PB, PE, RN", 5: "BA, SE", 6: "MG", 7: "ES, RJ", 8: "SP", 9: "PR, SC",
}


def cpf_valido(digitos: str) -> bool:
    """Valida os 2 dígitos verificadores do CPF (só números, 11 dígitos)."""
    if not re.fullmatch(r"\d{11}", digitos) or digitos == digitos[0] * 11:
        return False
    nums = [int(c) for c in digitos]
    d1 = (sum(n * (10 - i) for i, n in enumerate(nums[:9])) * 10 % 11) % 10
    d2 = (sum(n * (11 - i) for i, n in enumerate(nums[:10])) * 10 % 11) % 10
    return nums[9] == d1 and nums[10] == d2


def regiao_fiscal(digitos: str) -> str:
    return REGIAO_FISCAL.get(int(digitos[8]), "-")


def telefone_normalizado(digitos: str) -> str | None:
    """Devolve o telefone só com DDD+número (10 ou 11 dígitos) ou None se não parecer telefone BR."""
    d = digitos
    if len(d) in (12, 13) and d.startswith("55"):
        d = d[2:]
    if len(d) not in (10, 11) or int(d[:2]) not in DDDS:
        return None
    if len(d) == 11 and d[2] != "9":  # celular com 9 dígitos começa com 9
        return None
    return d


def analisar(texto: str) -> tuple[str, str]:
    t = texto.strip()
    tl = t.lower()

    # Comandos em texto livre
    if tl in ("/gerar_cpf", "gerar cpf"):
        return "gerar_cpf", t
    if tl in ("/gerar_cnpj", "gerar cnpj"):
        return "gerar_cnpj", t
    if tl in ("/temp", "temporario", "temporário", "temporarios", "temporários"):
        return "temp", t

    # Prefixo explícito: força o tipo e entrega o valor já sem o prefixo
    for prefixo, tipo in PREFIXOS.items():
        if tl.startswith(prefixo):
            return tipo, t[len(prefixo):].strip()

    # Redes sociais (@usuario)
    if t.startswith("@") and len(t) > 1:
        return "rede_social", t

    # URLs
    if tl.startswith(("http://", "https://")):
        caminho = tl.split("?")[0].split("#")[0]
        if caminho.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")) or "lens.google" in tl:
            return "imagem", t
        return "dominio", t

    # E-mail (antes das palavras-chave de imóveis, para não confundir "bens" dentro do e-mail)
    if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", t):
        return "email", t

    alnum = re.sub(r"[^0-9A-Za-z]", "", t).upper()
    digitos = re.sub(r"\D", "", t)

    # CNPJ (aceita o novo formato alfanumérico)
    if re.fullmatch(r"[0-9A-Z]{12}[0-9]{2}", alnum) and (len(digitos) == 14 or re.search(r"[A-Za-z]", t)):
        return "cnpj", t

    # Números: CPF x telefone
    if re.fullmatch(r"\d{3}\.\d{3}\.\d{3}-\d{2}", t):
        return "cpf", t
    if re.fullmatch(r"[\d\s().+-]+", t):
        tel = telefone_normalizado(digitos)
        cpf_ok = len(digitos) == 11 and cpf_valido(digitos)
        if cpf_ok and tel:
            return "cpf_ou_telefone", t  # serve para os dois: o bot pede o prefixo cpf: ou tel:
        if cpf_ok:
            return "cpf", t
        if tel:
            return "telefone", t

    if re.fullmatch(r"\d{5}-?\d{3}", t):
        return "cep", t

    if re.fullmatch(r"\d{6}-?\d?", t) or (len(digitos) == 7 and "-" in t):
        return "fipe", t

    # Placa antiga (ABC1234) e Mercosul (ABC1D23)
    if re.fullmatch(r"[A-Z]{3}\d[A-Z0-9]\d{2}", alnum) and len(alnum) == len(re.sub(r"[\s-]", "", t)):
        return "placa", t

    # Domínio (corrigido: a regex antiga aceitava o caractere "(")
    if re.fullmatch(r"([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}", t):
        return "dominio", t

    # Palavras-chave de imóveis/inventário por último, só como palavra inteira
    if RE_IMOVEIS.search(t):
        return "imoveis", t

    return "desconhecido", t


def detectar(texto: str) -> str:
    """Compatibilidade: devolve só o tipo."""
    return analisar(texto)[0]
