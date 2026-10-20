"""Checklist de inventário: onde procurar os bens de um falecido, pelos canais oficiais.

Cada item tem status (pendente / feito) e uma nota livre, guardados no SQLite (tabela inventario).
Confira sempre os requisitos de cada órgão (em geral: certidão de óbito, documentos do
inventariante e do falecido). Isto é um roteiro de organização, não aconselhamento jurídico.
"""

# (título, o que buscar, link ou onde ir)
ITENS = [
    ("Imóveis (certidão de bens)", "Pesquisa de bens imóveis por CPF em todo o Brasil (serviço pago).", "https://ridigital.org.br"),
    ("Imóveis (ONR / cartórios)", "Certidão de matrícula/ônus no cartório de cada imóvel conhecido.", "https://registradores.onr.org.br"),
    ("Dinheiro esquecido (Banco Central)", "Valores a receber de pessoa falecida (bancos, consórcios, tarifas).", "https://valoresareceber.bcb.gov.br"),
    ("Contas e relacionamentos bancários", "Registrato: instituições em que ele tinha conta ou crédito.", "https://registrato.bcb.gov.br"),
    ("Investimentos (B3)", "Posição em ações, FIIs, Tesouro e outros na custódia da B3.", "https://www.investidor.b3.com.br"),
    ("Imposto de Renda", "Última declaração dele: lista bens, dívidas e dependentes (e-CAC, com procuração/inventariante).", "https://cav.receita.fazenda.gov.br"),
    ("Veículos", "Detran do estado dele, pelo CPF: veículos, multas e débitos.", "Site do Detran do estado"),
    ("Empresas (sócio/titular)", "Junta Comercial: empresas em que ele era sócio. Depois consulte o CNPJ aqui no bot.", "Junta Comercial do estado"),
    ("Testamento", "Busca no Registro Central de Testamentos (CENSEC).", "https://www.censec.org.br"),
    ("INSS / benefícios", "Benefícios a receber, pensão por morte, valores não sacados.", "https://meu.inss.gov.br"),
    ("FGTS e PIS/PASEP", "Saldos em nome do falecido (Caixa / Banco do Brasil).", "https://www.caixa.gov.br"),
    ("Seguros e previdência privada", "Seguro de vida, previdência (consulta no site da SUSEP: Sistema de Registro de Seguros).", "https://www.gov.br/susep"),
]

FEITO = "feito"
PENDENTE = "pendente"


def render(estado: dict) -> str:
    """estado: {numero_do_item: (status, nota)} -> texto do checklist."""
    linhas = ["CHECKLIST DE INVENTÁRIO", ""]
    feitos = 0
    for i, (titulo, oque, onde) in enumerate(ITENS, start=1):
        status, nota = estado.get(i, (PENDENTE, None))
        marca = "[x]" if status == FEITO else "[ ]"
        feitos += status == FEITO
        linhas.append(f"{marca} {i}. {titulo}")
        linhas.append(f"    {oque}")
        linhas.append(f"    -> {onde}")
        if nota:
            linhas.append(f"    Nota: {nota}")
    linhas += [
        "",
        f"Progresso: {feitos}/{len(ITENS)}",
        "Comandos: /feito N  |  /pendente N  |  /nota N texto",
    ]
    return "\n".join(linhas)


def item_valido(n: int) -> bool:
    return 1 <= n <= len(ITENS)
