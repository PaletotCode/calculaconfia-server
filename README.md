🚀 CalculaConfia API (FastAPI)
API de produção para venda e consumo de créditos com integração Mercado Pago (PIX), verificação de conta por e-mail, sistema de indicação (uso único), cálculo com débito de créditos, Celery (e-mails), Redis (cache) e PostgreSQL.

Versão: 2.0.0

Sumário
- Visão Geral
- Stack e Serviços
- Configuração e Execução (Docker)
- Variáveis de Ambiente
- Migrações (Alembic)
- Logs e Observabilidade
- Regras de Negócio
- Integração Mercado Pago (PIX)
- Endpoints (APIs) com exemplos
- Banco de Dados (entidades)
- Troubleshooting
 - Cálculo Refatorado (ICMS no PIS/COFINS)
 - Dados Econômicos (IPCA/SELIC) e Seed
 - Guia Railway (Deploy + Seed from scratch)

Observação: fluxo é email‑only (telefone removido).

## Visão Geral
- Cadastro por e-mail + senha, verificação via código por e-mail.
- Login JWT (email como `sub`).
- Créditos comprados via Checkout Pro (PIX) e creditados pelo webhook “approved”.
- Sistema de indicação (uso único global) com bônus na primeira compra do indicado (+1 para cada lado).
- Cálculo consome 1 crédito e registra transação de uso.

## Stack e Serviços
- FastAPI, Uvicorn, Pydantic
- SQLAlchemy Async + PostgreSQL
- Alembic (migrações)
- Redis + Celery (SendGrid)
- Mercado Pago SDK
- Structlog

## Configuração e Execução (Docker)
1) `.env` (principais)
- SECRET_KEY=troque-em-producao
- ENVIRONMENT=development
- SENDGRID_API_KEY=… (se ausente, e-mails são simulados)
- MERCADO_PAGO_ACCESS_TOKEN=APP_USR-...
- MERCADO_PAGO_WEBHOOK_SECRET=… (opcional)
- PUBLIC_BASE_URL=https://<seu-domínio-ou-ngrok>
- FRONTEND_URL=http://localhost:3000
- MERCADO_PAGO_SELLER_EMAIL=<opcional para evitar autopagamento>
- ALLOWED_HOSTS=api.calculaconfia.com.br,novo-subdominio.calculaconfia.com.br

2) Subir
```
docker compose down -v
docker compose build --no-cache
docker compose up -d postgres redis
docker compose up -d api
```

3) Migrações
```
docker compose exec api alembic heads
docker compose exec api alembic upgrade head
```

4) Worker e ferramentas
```
docker compose up -d celery_worker celery_beat redis_insight
```

5) Saúde
```
curl http://localhost:8000/api/v1/health
```

Importante: `PUBLIC_BASE_URL` deve estar correto ANTES de criar uma ordem (preferência usa o valor atual). Mudou o ngrok ou o domínio? Atualize também `FRONTEND_URL` e `ALLOWED_HOSTS` e crie nova ordem.

## Variáveis de Ambiente
Principais: `DATABASE_URL`, `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`, `SECRET_KEY`, `ENVIRONMENT`, `SENDGRID_API_KEY`, `MAIL_FROM`, `MAIL_FROM_NAME`, `MERCADO_PAGO_ACCESS_TOKEN`, `MERCADO_PAGO_WEBHOOK_SECRET` (opcional), `MERCADO_PAGO_SELLER_EMAIL` (opcional), `PUBLIC_BASE_URL`, `FRONTEND_URL`, `ALLOWED_HOSTS`.

## Logs e Observabilidade
- Todos: `docker compose logs -f`
- API: `docker compose logs -f api`
- Worker: `docker compose logs -f celery_worker`
- Filtro (PowerShell): `docker compose logs -f api | Select-String -Pattern "webhook|approved|credits" -AllMatches`

## Migrações (Alembic)
- Ver heads: `docker compose exec api alembic heads`
- Aplicar: `docker compose exec api alembic upgrade head`
- IDs longos foram encurtados para caber no `varchar(32)` da tabela `alembic_version`.

## Regras de Negócio
- Cadastro: email obrigatório/único; `applied_referral_code` opcional (uso único global).
- Verificação: via código de 6 dígitos enviado por e-mail.
- Login: apenas usuários `is_active` e `is_verified`.
- Indicação (uso único): código gerado na primeira compra do dono; o código só pode ser usado 1 vez no cadastro de um terceiro. Bônus aplicado na primeira compra do indicado (+1 indicado, +1 indicador). Limite do indicador: 1 crédito total.
- Créditos: expiração — purchase: 40 dias; referral_bonus: 60 dias. Saldo válido ignora expirados.

## Integração Mercado Pago (PIX)
- Fluxo: create-order → checkout → webhook approved → crédito.
- Webhook `POST/GET /api/v1/payments/webhook` aceita `payment` e `merchant_order`.
- Créditos por pagamento:
  - Primeiro tenta `metadata.credits_amount` no `payment`;
  - Fallback: busca `merchant_order` e infere pelo item (`CREDITS-PACK-3` → 3).
- Idempotência por `payment_id` (reference_id `mp_<payment_id>`).
- Autopagamento bloqueado se `MERCADO_PAGO_SELLER_EMAIL` == e-mail do pagador.

## Endpoints (APIs)
Autenticação: JWT armazenado em cookie HTTP-only `access_token`.

- POST `/api/v1/register` (público)
  ```json
  {
    "email": "user@example.com",
    "password": "SenhaForte123!",
    "first_name": "Nome",
    "last_name": "Sobrenome",
    "applied_referral_code": null
  }
  ```

- POST `/api/v1/auth/send-verification-code` (público)
  ```json
  { "email": "user@example.com" }
  ```

- POST `/api/v1/auth/verify-account` (público)
  ```json
  { "email": "user@example.com", "code": "123456" }
  ```

  - POST `/api/v1/login` (público; form; gera cookie `access_token`)
  - `username=<email>&password=<senha>`

- GET `/api/v1/me` (auth)
- POST `/api/v1/payments/create-order` (auth)
- POST/GET `/api/v1/payments/webhook` (público; Mercado Pago)
- GET `/api/v1/credits/balance` (auth)
- GET `/api/v1/credits/history` (auth)
- GET `/api/v1/referral/stats` (auth)
- POST `/api/v1/calcular` (auth)
  ```json
  {
    "bills": [
      { "icms_value": 1000.0, "issue_date": "2024-08" },
      { "icms_value": 1500.0, "issue_date": "2024-09" }
    ]
  }
  ```
- GET `/api/v1/health` | `/api/v1/health/detailed`

## Banco de Dados (principais)
- users: email, hashed_password, first_name, last_name, referral_code, referred_by_id, referral_credits_earned, credits (legado), is_verified, is_active, is_admin, created_at, updated_at
- verification_codes: identifier(email), code(6), type(EMAIL), used, expires_at, created_at
- credit_transactions: user_id, transaction_type(purchase/usage/referral_bonus), amount, balance_before/after, reference_id(`mp_<payment_id>`), expires_at, created_at
- query_histories, audit_logs, selic_rates

## Troubleshooting
- PIX cinza: evite autopagamento; configure `MERCADO_PAGO_SELLER_EMAIL`.
- Webhook não chega: publique URL (`PUBLIC_BASE_URL`) antes de criar a ordem; reenvie notificação; teste manual com `?topic=payment&id=...`.
- Créditos duplicados: idempotência por payment_id; duas compras diferentes somam corretamente.
- Alembic: conflitos de heads/IDs longos → `alembic heads` e `alembic upgrade head`.

## Domínio de Produção
- Domínio: calculaconfia.com.br
- Produção sugerida:
  - API: `https://api.calculaconfia.com.br`
  - FRONTEND_URL: `https://calculaconfia.com.br`
  - ALLOWED_HOSTS: `api.calculaconfia.com.br,novo-subdominio.calculaconfia.com.br`
  - Webhook no MP: `https://api.calculaconfia.com.br/api/v1/payments/webhook`

Para diretrizes do frontend, consulte `FRONTEND.md`.

## Cálculo Refatorado (ICMS no PIS/COFINS)
- Entrada do usuário (até 12 faturas):
  - `icms_value` (valor do ICMS da fatura)
  - `issue_date` no formato `YYYY-MM` (mês/ano)
- Linha do tempo do cálculo: 120 meses encerrando no mês mais recente informado.
- Passos do algoritmo:
  1. ICMS_BASE = média dos `icms_value` informados.
  2. Reconstrução do ICMS por IPCA: ancora ICMS_BASE no primeiro mês da janela e aplica o IPCA mês a mês para frente; meses informados são sobrescritos pelos valores reais.
  3. Indevido mensal: `indevido[m] = ICMS[m] * 0.037955`.
  4. Atualização pela SELIC: aplica fator cumulativo até o mês final. Exceção: meses informados (reais) não recebem correção (fator = 1.0).
  5. Resultado: soma de todos os meses corrigidos.
- Implementação:
  - Motor: `app/services/calculation_engine.py`.
  - Serviço: `CalculationService.execute_calculation_for_user` em `app/services/main_service.py`.
  - Modelos: `SelicRate` e `IPCARate` em `app/models_schemas/models.py`.

### API de Cálculo
- Endpoint: `POST /api/v1/calcular` (auth Bearer)
- Body:
  ```json
  {
    "bills": [
      { "icms_value": 105, "issue_date": "2025-06" },
      { "icms_value": 110, "issue_date": "2025-07" },
      { "icms_value": 120, "issue_date": "2025-08" }
    ]
  }
  ```
- Resposta:
  ```json
  {
    "valor_calculado": 1234.56,
    "creditos_restantes": 2,
    "calculation_id": 987,
    "processing_time_ms": 42
  }
  ```

## Dados Econômicos (IPCA/SELIC) e Seed
### Tabelas
- `ipca_rates(year, month, rate)` — `rate` em fração mensal (ex.: 0,40% => 0.0040).
- `selic_rates(year, month, rate)` — `rate` em fração mensal (ex.: 1,16% => 0.0116).

### Arquivo do IPCA
- Path no repositório: `app/ipca_mensal.csv`.
- Formato: CSV `;` com cabeçalho `data;valor`.
- Datas aceitas: `DD/MM/AAAA`, `AAAA-MM-DD`, `MM/AAAA`, `AAAA-MM`.
- Valor: percentual mensal (`,` ou `%` aceitos). Ex.: `0,40` => 0.40%.

### Comandos Locais (host)
- Ajuste `DATABASE_URL` para o Postgres do Docker (mapeado em `15432`):
  - `.env` (host):
    - `DATABASE_URL=postgresql+asyncpg://torres_user:torres_password@localhost:15432/torres_db`
- Criar tabelas e popular IPCA/SELIC:
```
python app/scripts/manage.py create-tables
python app/scripts/manage.py seed-ipca app/ipca_mensal.csv
python app/scripts/manage.py seed-selic selic.txt
```

### Comandos via Docker (dentro do container API)
```
docker compose exec api python app/scripts/manage.py create-tables
docker compose exec api python app/scripts/manage.py seed-ipca app/ipca_mensal.csv
docker compose exec api python app/scripts/manage.py seed-selic selic.txt
```

## Guia Railway (Deploy + Seed from scratch)
Pré‑requisitos:
- Conta Railway e Railway CLI (`npm i -g @railway/cli`), `railway login`, `railway link` no projeto.
- Banco Postgres no Railway (Provisionar plugin Postgres) ou usar sua própria instância.

### Variáveis de Ambiente no Railway
- Configure em “Variables” (ou `railway variables set`):
  - `DATABASE_URL` (string de conexão do Postgres gerado pelo Railway)
  - `REDIS_URL` (se usar Redis gerenciado)
  - Outras: `SECRET_KEY`, `ENVIRONMENT=production`, `SENDGRID_API_KEY`, `PUBLIC_BASE_URL`, `FRONTEND_URL`, etc.

### Deploy da API
- Faça deploy do repositório (GitHub) ou via CLI.
- Certifique‑se de que o build instale dependências e inicie `uvicorn app.main:app`.

### Criar Tabelas em Produção (Railway)
- Execute o comando dentro do serviço API (com as variáveis já aplicadas):
```
railway run "python app/scripts/manage.py create-tables"
```
  - Alternativa: abrir um shell `railway shell` e rodar os comandos manualmente.

### Popular IPCA (Railway)
- Como `app/ipca_mensal.csv` está no repositório, o arquivo estará no container no mesmo caminho.
- Rode:
```
railway run "python app/scripts/manage.py seed-ipca app/ipca_mensal.csv"
```

### Popular SELIC (Railway)
- Suba o arquivo `selic.txt` para o repositório (ou empacote via assets) para ficar disponível em `app/selic.txt`.
- Rode:
```
railway run "python app/scripts/manage.py seed-selic app/selic.txt"
```

### Testar o Cálculo em Produção
- Faça login no app, adquira créditos, e chame `POST /api/v1/calcular` com o body do exemplo acima.

### Dicas
- Erros de conexão: verifique `DATABASE_URL` e acessibilidade do Postgres.
- CSV de IPCA: confirme `data;valor` e encoding UTF‑8 (BOM aceito).
- Caso algum mês de SELIC não exista, o cálculo usa 0% para aquele mês (recomendado popular o período completo).
