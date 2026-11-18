# Estoque Pro — controle de estoque para distribuidoras

Sistema web (Django 5) de controle de estoque com cara de ERP: produtos, depósitos, movimentações com
kardex, pedidos de compra, inventário, alertas, curva ABC, painel com gráficos, exportação CSV e API JSON.
Interface em português do Brasil, responsiva (celular incluso), com tema claro/escuro e **sem nenhum recurso
externo (CDN)**: todo CSS, JS e gráficos (SVG) são próprios.

É um projeto de **portfólio/demonstração**: todos os dados são fictícios (distribuidora "Aurora").

## O que ele faz

- **Cadastros**: produtos (SKU, categoria, unidade, custo, preço, estoque mínimo, foto opcional), categorias, fornecedores e depósitos.
- **Movimentações**: entrada (compra), saída (venda, consumo, perda), ajuste e transferência entre depósitos.
  O **saldo nunca é editável**: é sempre a soma das movimentações (livro-razão imutável). Kardex por produto com saldo corrente.
- **Pedidos de compra**: rascunho → enviado → recebido; o recebimento gera as entradas e atualiza o último custo.
  Também é possível gerar rascunhos automáticos (um por fornecedor) a partir dos alertas.
- **Alertas**: abaixo do mínimo, ruptura e sem giro (capital parado), com sugestão de compra.
- **Curva ABC** pelo valor consumido nos últimos 90 dias e **valor total do estoque**.
- **Painel**: KPIs e gráficos (compras x vendas, evolução do valor, valor por categoria, mais vendidos).
- **Inventário**: contagem física por depósito; ao finalizar, as divergências viram ajustes.
- **Papéis**: administrador, operador e somente leitura. Busca, filtros, paginação e exportação CSV nas listas.
- **API JSON somente leitura** com token: `/api/produtos/` e `/api/saldo/`.

## Como rodar localmente

Requer Python 3.12+ (testado também em 3.14).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export DEBUG=1                      # desenvolvimento
python manage.py migrate
python manage.py seed_demo          # ~80 produtos, 6 meses de movimentações
python manage.py runserver
```

Abra http://127.0.0.1:8000 e clique em **Entrar na demonstração**. Sem `DATABASE_URL`, usa SQLite
(`db.sqlite3`; em `/data/db.sqlite3` se o diretório `/data` existir).

```bash
python manage.py test                      # testes automatizados
python manage.py seed_demo --reset         # recria os dados de estoque
python manage.py seed_demo --se-vazio      # só popula se estiver vazio (usado no release)
```

### Docker

```bash
docker build -t estoque .
docker run --rm -p 8000:8000 -e DJANGO_SECRET_KEY=troque-por-um-valor-longo estoque
```

A imagem usa `python:3.12-slim`, roda como usuário não root, executa `collectstatic` no build e escuta em `$PORT`
(padrão 8000). No `Procfile`, o `release` roda `migrate` e `seed_demo --se-vazio`; o `web` sobe o gunicorn.
O arquivo `ENV` define `HEALTHCHECK_PATH=/saude/`.

## Variáveis de ambiente

| Variável | Padrão | Descrição |
|---|---|---|
| `DJANGO_SECRET_KEY` (ou `SECRET_KEY`) | gerada e salva em arquivo | Chave secreta. **Defina em produção.** Sem ela, uma chave é gerada em `/data/.secret_key` ou `.secret_key` (fora do git). |
| `DEBUG` | `0` | `1` liga o modo debug (e desliga cookies seguros). |
| `DATABASE_URL` | SQLite local | Ex.: `postgres://usuario:senha@host:5432/banco`. |
| `DJANGO_ALLOWED_HOSTS` | `.satela.com.br,localhost,127.0.0.1` | Hosts aceitos, separados por vírgula. |
| `CSRF_TRUSTED_ORIGINS` | derivado dos hosts | Origens extras; `https://*.satela.com.br` etc. são derivadas automaticamente. |
| `DEMO_MODE` | `1` | Botão "Entrar na demonstração" + faixa de aviso. Use `0` para uma instalação real. |
| `DEMO_ADMIN_PASSWORD` | vazio | Se definida, `seed_demo` cria o superusuário `admin` com essa senha. Se vazia, **nenhum** superusuário é criado. |
| `PORT` | `8000` | Porta do gunicorn. |
| `SECURE_SSL_REDIRECT` | `0` | `1` força HTTPS (a rota `/saude/` é isenta). O proxy deve enviar `X-Forwarded-Proto`. |
| `SECURE_HSTS_SECONDS` | `3600` | HSTS fora do debug. |
| `DIAS_SEM_GIRO` | `60` | Janela do alerta "sem giro". |
| `LOGIN_MAX_TENTATIVAS` / `LOGIN_JANELA_SEGUNDOS` | `6` / `300` | Limite de tentativas de login por IP e por usuário. |

Mídia (fotos): gravada em `/data/media` se `/data` existir; caso contrário, em `./media`. Uploads limitados a 2 MB.

## Papéis

| Papel | Pode |
|---|---|
| Somente leitura | Consultar tudo e exportar CSV. |
| Operador | Tudo do leitura + lançar movimentações, transferências, compras, inventários, cadastrar/editar. |
| Administrador | Tudo do operador + excluir registros e gerenciar papéis em **Usuários e papéis**. |

Na demonstração, o botão entra como `demo` (operador) e há o atalho "Ver como somente leitura".

## API

```bash
curl -H "Authorization: Bearer <token>" https://seu-host/api/produtos/?situacao=minimo
curl -H "Authorization: Bearer <token>" "https://seu-host/api/saldo/?sku=MER-0001"
```

O token é exibido em **API** (menu lateral) depois do login. Respostas paginadas (`page`, `page_size`); sem token: 401.

## Roteiro de demonstração (2 min)

1. **Entrada (10 s)** — abra o endereço e clique em **Entrar na demonstração**. Aponte a faixa "dados fictícios".
2. **Painel (30 s)** — valor total do estoque, vendas dos últimos 30 dias, cartões de alerta (abaixo do mínimo, ruptura, sem giro),
   gráficos de compras x vendas e de valor por categoria. Alterne o tema claro/escuro no topo.
3. **Alertas (25 s)** — menu **Alertas** → aba *Abaixo do mínimo*; mostre a sugestão de compra e clique em
   **Gerar rascunhos de compra** (cria um pedido por fornecedor). Abra um e clique em **Enviar** e depois **Registrar recebimento**.
4. **Kardex (25 s)** — abra o produto reposto: o saldo subiu pela entrada gerada, com histórico e saldo corrente.
   Use **Transferir** entre depósitos e veja o saldo por depósito mudar sem alterar o total.
5. **Inventário (15 s)** — **Inventário** → abra o que está em contagem, lance uma diferença e finalize: aparece o ajuste no kardex.
6. **Análises e integração (15 s)** — **Curva ABC** (classes A/B/C), exporte um **CSV** e mostre a página **API** com o token e o `curl`.
7. **Celular (opcional)** — redimensione a janela: menu recolhível e tabelas roláveis.

## Segurança e limitações honestas

- CSRF ativo em todos os formulários; sessões e CSRF com cookies seguros fora do debug; `X-Forwarded-Proto` respeitado.
- Rate limit de login simples, em memória do processo (cada worker do gunicorn mantém o seu contador; não é um WAF).
- A API aceita o token só no cabeçalho (nunca na URL) e é somente leitura.
- Quantidades são inteiras (sem frações de quilo/litro). Um só custo por produto (último custo de compra; sem custo médio/PEPS).
- Sem multiempresa, lotes/validade, numeração serial, nem envio real de e-mail ou integração fiscal (o "enviar pedido" apenas muda o status).
- Fotos são servidas pelo próprio Django (`/media/`), adequado para demonstração; em produção real use armazenamento de objetos.
- Os cálculos de saldo são feitos por agregação no banco: ótimo para milhares de produtos, mas um ERP com milhões de movimentações pediria saldos materializados.
