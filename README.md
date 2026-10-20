# Buscador Poderoso Unificado

Você envia **um dado** no Telegram e o bot reconhece o tipo, consulta várias fontes **ao mesmo
tempo** e devolve um relatório só. Se uma fonte cair, as outras continuam.

| Você envia | O relatório traz |
|---|---|
| CNPJ | empresa, sócios, porte, capital, Simples/MEI, telefones, **endereço pelo CEP + mapa**, **domínio do e-mail da empresa** (registro, DNS, SPF/DMARC, site) |
| CEP | endereço, DDD, código IBGE, mapa |
| domínio / site | registro (RDAP), IP e hospedagem, NS, MX, SPF, DMARC, resposta e título do site |
| e-mail | provedor, MX, Gravatar (contas vinculadas), **vazamentos conhecidos** (HIBP com chave, ou XposedOrNot grátis), GitHub com e-mail público, **perfis com o mesmo @** do e-mail, busca na web |
| telefone | formato, fixo/celular, DDD, estado e cidades |
| CPF | validação dos dígitos e região fiscal (não consulta base cadastral) |
| placa | formato antigo/Mercosul e equivalente, dados do provedor (se configurado), links oficiais |
| código FIPE | marca, modelo e valores por ano |
| @usuario | varredura em 18 sites verificáveis (achou / não achou / inconclusivo) + links manuais para Instagram, X, TikTok, Facebook e LinkedIn |
| link de imagem | pesquisa reversa |
| matrícula / inventário / imóvel | orientação patrimonial + `/inventario` |

Prefixos resolvem ambiguidade: `cpf:`, `tel:`, `email:`, `cnpj:`, `cep:`, `placa:`, `user:`.

Comandos: `/inventario`, `/feito N`, `/pendente N`, `/nota N texto`, `/historico`, `/gerar_cpf`,
`/gerar_cnpj`, `/temp`, `/meuid`.

## Placa: como conectar um provedor
O bot não faz scraping nem contorna captcha. Para trazer marca/modelo/ano/cor, configure no `.env`
uma API que você tenha acesso (com chave), sem alterar o código:
```
# GET (chave em Authorization: Bearer)
PLACA_API_URL=https://seu-provedor.exemplo/api/placa/{placa}
PLACA_API_KEY=sua_chave

# POST (chave em outro cabeçalho, sem prefixo)
PLACA_API_URL=https://seu-provedor.exemplo/consulta
PLACA_API_METHOD=POST
PLACA_API_BODY={"placa": "{placa}"}
PLACA_API_KEY=sua_chave
PLACA_API_KEY_HEADER=x-api-key
PLACA_API_KEY_PREFIX=
```
O bot reconhece campos comuns (marca, modelo, ano, cor, uf, município, chassi...), inclusive dentro
de `dados`, `data`, `resultado` ou `veiculo`. Erros do provedor (401, 403, 429) aparecem no relatório.
Sem provedor, mostra a análise da placa e os links oficiais (Senatran, Detran, Carteira Digital).

## Instalação (Windows)
```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```
Crie o `.env` a partir do `env.example` (token, ID, banco). Para descobrir seu ID: deixe
`ALLOWED_USER_IDS=` vazio, rode o bot e envie `/meuid`. Rodar: `python bot.py` (Ctrl+C para parar).

## Garantias
- `ALLOWED_USER_IDS` preenchido: só esses IDs usam o bot. Vazio: aberto (aviso no terminal).
- Auditoria mascara e-mail, telefone, CPF e @; registros com mais de 90 dias são apagados ao iniciar.
- Cache de 24h nas consultas de CNPJ.

## Atenção
- Nunca publique o `.env` nem o `bot.db`. Se o token vazar, use `/revoke` no BotFather.
- Conversas com bots no Telegram não têm criptografia de ponta a ponta.
- O checklist de inventário é roteiro de organização, não aconselhamento jurídico.
