"""Consulta de CNPJ (dados públicos da Receita Federal) via BrasilAPI."""
import re

import httpx

API_URL = "https://brasilapi.com.br/api/cnpj/v1/{cnpj}"

W1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
W2 = [6] + W1


class CnpjNotFound(Exception):
    pass


def normalize(raw: str) -> str:
    """Remove pontuação. Aceita letras porque o novo CNPJ pode ser alfanumérico."""
    return re.sub(r"[^0-9A-Za-z]", "", raw).upper()


def _digit(base: str, weights: list[int]) -> str:
    total = sum((ord(ch) - 48) * w for ch, w in zip(base, weights))
    rest = total % 11
    return "0" if rest < 2 else str(11 - rest)


def is_valid(cnpj: str) -> bool:
    """Valida o formato e os dois dígitos verificadores."""
    if not re.fullmatch(r"[0-9A-Z]{12}[0-9]{2}", cnpj):
        return False
    if cnpj == cnpj[0] * 14:
        return False
    return cnpj[12] == _digit(cnpj[:12], W1) and cnpj[13] == _digit(cnpj[:13], W2)


def pretty(cnpj: str) -> str:
    return f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"


async def fetch(cnpj: str, client: httpx.AsyncClient) -> dict:
    resp = await client.get(API_URL.format(cnpj=cnpj), timeout=15)
    if resp.status_code == 404:
        raise CnpjNotFound(cnpj)
    resp.raise_for_status()
    return resp.json()


def format_company(cnpj: str, d: dict) -> str:
    endereco = ", ".join(p for p in [d.get("logradouro"), d.get("numero"), d.get("bairro")] if p)
    cidade = " - ".join(p for p in [d.get("municipio"), d.get("uf")] if p)
    lines = [
        f"Empresa: {d.get('razao_social') or '-'}",
        f"Nome fantasia: {d.get('nome_fantasia') or '-'}",
        f"CNPJ: {pretty(cnpj)}",
        f"Situação: {d.get('descricao_situacao_cadastral') or '-'}",
        f"Início da atividade: {d.get('data_inicio_atividade') or '-'}",
        f"Atividade principal: {d.get('cnae_fiscal_descricao') or '-'}",
        f"Endereço: {endereco or '-'}",
        f"Cidade: {cidade or '-'}  CEP: {d.get('cep') or '-'}",
    ]
    socios = d.get("qsa") or []
    if socios:
        lines += ["", "Sócios e administradores:"]
        for s in socios[:10]:
            lines.append(f"- {s.get('nome_socio') or '-'} ({s.get('qualificacao_socio') or '-'})")
        if len(socios) > 10:
            lines.append(f"... e mais {len(socios) - 10}")
    lines += ["", "Fonte: Receita Federal, via BrasilAPI"]
    return "\n".join(lines)
