CalculaConfia Frontend (Guia de Implementação)

Objetivo
- Entregar um frontend que cubra 100% dos fluxos suportados pelo backend, com UX clara para compra de créditos (PIX), verificação de conta via e-mail, uso de indicação (uso único), consumo de créditos por cálculo e exibição de histórico/estatísticas.

Stack sugerida
- Next.js (App Router) + TypeScript
- State: React Query ou SWR
- UI: TailwindCSS ou Lib de sua preferência
- Autenticação: JWT Bearer armazenado em `localStorage`/`secure storage` (usar HTTPS em produção)
- Integração com Mercado Pago: redirecionar para `init_point` (Checkout Pro)

Ambiente
- Produção: `calculaconfia.com.br`
  - FRONTEND_URL: `https://calculaconfia.com.br`
  - API: `https://api.calculaconfia.com.br`
- Dev: `http://localhost:3000`
  - API local: `http://localhost:8000`

Variáveis
- NEXT_PUBLIC_API_BASE_URL
- (Opcional) NEXT_PUBLIC_ENV=development|production

Arquitetura de Páginas (rotas)
- `/register` — formulário com email, senha, nome, sobrenome e campo opcional “Código de indicação”.
- `/verify` — formulário com email e código (6 dígitos).
- `/login` — formulário de acesso (email/senha).
- `/dashboard` — mostra créditos, referral_code (quando houver), botão “Comprar créditos”.
- `/payment/pending` — instruções enquanto pagamento está processando; botão “Verificar saldo”.
- `/payment/success` — confirmação; instruir usuário a checar saldo.
- `/payment/failure` — falha; call-to-action para tentar novamente.
- `/calcular` — formulário que aceita as faturas (icms_value e issue_date). Mostra resultado e atualiza créditos.
- `/historico` — lista consultas anteriores e transações de crédito.
- (Admin opcional) `/admin` — estatísticas gerais (se role admin disponível).

Fluxos
1) Registro
   - POST `/api/v1/register` com { email, password, first_name, last_name, applied_referral_code? }.
   - Exibir aviso para verificar o e-mail. Se quiser, botão “Reenviar código”.

2) Verificação
   - POST `/api/v1/auth/verify-account` com { email, code }.
   - Após sucesso, redirecionar para `/login`.

3) Login
   - POST `/api/v1/login` (form) com username=email, password.
   - Salvar o token (e.g. localStorage). Definir Authorization em todas as requisições seguintes.

4) Dashboard
   - GET `/api/v1/me` — exibir créditos atuais e, após a primeira compra, `referral_code`.
   - GET `/api/v1/referral/stats` — exibir quantos créditos de indicação já obteve (máx. 1) e total de referidos.
   - Botão “Comprar créditos” → chama POST `/api/v1/payments/create-order` e redireciona para `init_point`.

5) Pagamento (Checkout Pro)
   - Redirecionar para `init_point` em nova aba ou mesma página.
   - Ao voltar, abrir `/payment/pending`. Mostrar instruções: “Seus créditos serão creditados em até alguns segundos”.
   - Incluir botão “Verificar saldo” que chama GET `/api/v1/credits/balance` em intervalos (polling leve) por até ~60s.
   - Quando saldo > anterior, exibir “Créditos recebidos!” e CTA para `/calcular`.

6) Cálculo
   - Formulário com múltiplos meses (até 12). Campos: `icms_value` (float), `issue_date` (YYYY-MM).
   - POST `/api/v1/calcular`. Em sucesso, mostrar `valor_calculado` e `creditos_restantes`.

7) Histórico e Transações
   - GET `/api/v1/credits/history` para listar transações (mostrar `transaction_type`, `amount`, `expires_at`, `created_at`).
   - GET `/api/v1/historico` para histórico de cálculos.

8) Indicação (uso único)
   - No `/register`, campo “Código de indicação (opcional)”.
   - Se o backend retornar 400 “Código já resgatado!”, exibir mensagem amigável e permitir cadastro sem referral.
   - Após a primeira compra do usuário, exibir o `referral_code` no `/dashboard` para compartilhar.

HTTP Client (exemplo)
```ts
const API = process.env.NEXT_PUBLIC_API_BASE_URL!;

async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem('token');
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
  const res = await fetch(`${API}${path}`, { ...options, headers });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
```

Exemplos de chamadas
- Registrar: `api('/api/v1/register', { method: 'POST', body: JSON.stringify(payload) })`
- Verificar: `api('/api/v1/auth/verify-account', { method: 'POST', body: JSON.stringify({ email, code }) })`
- Login (form):
```ts
async function login(email: string, password: string) {
  const body = new URLSearchParams({ username: email, password });
  const res = await fetch(`${API}/api/v1/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  });
  if (!res.ok) throw new Error(await res.text());
  const data = await res.json();
  localStorage.setItem('token', data.access_token);
  return data;
}
```

Compra de créditos
```ts
async function createOrder() {
  const { init_point } = await api<{ init_point: string }>(`/api/v1/payments/create-order`, { method: 'POST' });
  window.location.href = init_point; // ou abrir nova aba
}
```

Boas práticas
- Sempre ler `/me` após possível crédito para atualizar o referral_code (gerado após a primeira compra).
- Error handling: tratar status 400/401/402/500 com mensagens claras.
- Em produção, usar HTTPS e cookies `Secure`/`SameSite` se migrar de localStorage.

Domínio
- Produção: `calculaconfia.com.br` para frontend e `api.calculaconfia.com.br` para backend (sugerido).
- Configurar CORS no backend para o domínio do frontend.

