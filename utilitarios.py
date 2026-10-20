"""Módulo de utilitários: geradores de dados de teste (CPF/CNPJ) e ferramentas auxiliares."""
import random
import re


def gerar_cpf() -> str:
    """Gera um CPF válido para testes de sistemas."""
    def calcula_digito(digitos):
        soma = sum(d * (len(digitos) + 1 - i) for i, d in enumerate(digitos))
        resto = 11 - (soma % 11)
        return 0 if resto > 9 else resto

    base = [random.randint(0, 9) for _ in range(9)]
    d1 = calcula_digito(base)
    d2 = calcula_digito(base + [d1])
    cpf_list = base + [d1, d2]
    c = "".join(map(str, cpf_list))
    return f"{c[:3]}.{c[3:6]}.{c[6:9]}-{c[9:]}"


def gerar_cnpj() -> str:
    """Gera um CNPJ válido para testes de sistemas."""
    def calcula_digito(digitos, pesos):
        soma = sum(d * w for d, w in zip(digitos, pesos))
        resto = soma % 11
        return 0 if resto < 2 else 11 - resto

    base = [random.randint(0, 9) for _ in range(8)] + [0, 0, 0, 1]
    p1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    d1 = calcula_digito(base, p1)
    p2 = [6] + p1
    d2 = calcula_digito(base + [d1], p2)
    cnpj_list = base + [d1, d2]
    c = "".join(map(str, cnpj_list))
    return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}"


def info_temporarios() -> str:
    """Retorna orientações e links para serviços gratuitos de e-mails/telefones temporários."""
    return "\n".join([
        "Geradores de E-mails e Telefones Temporários (Throwaway Contacts):",
        "",
        "• E-mails Temporários recomendados:",
        "  - SimpleLogin (https://simplelogin.io)",
        "  - Temp Mail / Guerrilla Mail",
        "",
        "• Telefones Temporários para SMS:",
        "  - Use serviços públicos de recepção de SMS online (ex: SMSPool, AnonymSMS)",
        "",
        "Nota: Ferramentas de criação automática de caixa postal via API exigem serviços pagos ou chaves dedicadas."
    ])