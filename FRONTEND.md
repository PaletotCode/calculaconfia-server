CalculaConfia Frontend (Guia de Implementação)

Objetivo
- Entregar um frontend que cubra 100% dos fluxos suportados pelo backend, com UX clara para compra de créditos (PIX), verificação de conta via e-mail, uso de indicação (uso único), consumo de créditos por cálculo e exibição de histórico/estatísticas.

Stack sugerida
- Next.js (App Router) + TypeScript
- State: React Query ou SWR
- UI: TailwindCSS ou Lib de sua preferência
- Autenticação: JWT Bearer armazenado em `localStorage`/`secure storage` (usar HTTPS em produção)
- Integração com Mercado Pago: redirecionar para `init_point` (Checkout Pro)

SDK Mercado Pago (Frontend JS v2) — obrigatório
- Use o SDK oficial no frontend (MP.js v2) para criar/abrir o checkout de forma segura.
- Public key necessária (defina no ambiente do frontend): `NEXT_PUBLIC_MERCADO_PAGO_PUBLIC_KEY`.
- Inicialização básica (Next.js):
  1) Adicione o SDK no `_app` ou no componente de página (somente no cliente):
     ```tsx
     import Script from 'next/script'
     
     export default function RootLayout({ children }) {
       return (
         <html>
           <body>
             {children}
             <Script src="https://sdk.mercadopago.com/js/v2" strategy="afterInteractive" />
           </body>
         </html>
       )
     }
     ```
  2) Instancie o SDK e abra o checkout com a `preference_id` retornada do backend:
     ```tsx
     declare global { interface Window { MercadoPago: any } }
     
     const PUBLIC_KEY = process.env.NEXT_PUBLIC_MERCADO_PAGO_PUBLIC_KEY!
     
     async function openCheckout(preferenceId: string) {
       const mp = new window.MercadoPago(PUBLIC_KEY, { locale: 'pt-BR' })
       mp.checkout({ preference: { id: preferenceId } })
     }
     
     async function onBuyCredits() {
       // 1) Cria a preferência no backend
       const { preference_id, init_point } = await api('/api/v1/payments/create-order', { method: 'POST' })
       // 2) Abre pelo SDK (recomendado) OU redireciona para init_point
       if (window.MercadoPago) return openCheckout(preference_id)
       window.location.href = init_point
     }
     ```
  Observações:
  - Estamos usando somente PIX; ignore “Secure Fields” de cartões.
  - Em produção, garanta HTTPS (pode ser gerenciado por CDN/Cloud — ex.: Cloudflare). Não é necessário mexer no backend para isso.

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
   - Preferencial: abrir via SDK (mp.checkout) com `preference_id`.
   - Alternativa: redirecionar para `init_point` em nova aba ou mesma página.
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

Visão Geral

Base API: NEXT_PUBLIC_API_URL aponta para /api/v1 do backend (produção e dev).
Auth por JWT Bearer; endpoints já prontos.
Cálculo consome 1 crédito; compra via Mercado Pago (Checkout Pro).
Variáveis de Ambiente (Frontend)

NEXT_PUBLIC_API_URL: ex. dev http://localhost:8000/api/v1, prod https://calculaconfia-production.up.railway.app/api/v1
NEXT_PUBLIC_PUBLIC_BASE_URL (opcional): URL pública do site para links canônicos/SEO.
Cores (Design Tokens)

primary: #16a34a
primaryHover: #15803d
secondary: #ca8a04
slateDark: #1e293b
bgSoft: #f1f5f9
textMain: #0f172a
textLight: #f8fafc
Páginas/Rotas (MVP)

Público: / (landing), /login, /register, /verify, /reset-password, /reset-password/confirm
Autenticado: /dashboard, /calculator, /history, /credits, /referrals
Retornos de pagamento: /payment/success, /payment/failure, /payment/pending
Autenticação

Login: POST /login (form username/password) → retorna access_token, expires_in, user_info.
Header autenticado: Authorization: Bearer <token>.
Usuário atual: GET /me (retorna créditos válidos).
Registro: POST /register
Verificação: POST /auth/send-verification-code → POST /auth/verify-account
Reset de senha: POST /auth/request-password-reset → POST /auth/reset-password
Cálculo (ICMS no PIS/COFINS)

Endpoint: POST /calcular
Payload: {"bills":[{"icms_value":105,"issue_date":"2025-06"}, ...]} (até 12)
Resposta: {"valor_calculado": number, "creditos_restantes": number, "calculation_id": number, "processing_time_ms": number}
Histórico: GET /historico?limit=&offset=
Pagamentos (Mercado Pago)

Criar preferência: POST /payments/create-order → { preference_id, init_point }
Redirecionar usuário para init_point.
Webhook backend credita automaticamente (idempotente).
Páginas de retorno: /payment/success|failure|pending (somente UI; status real via webhook).
Regras/Validações (Frontend)

issue_date sempre YYYY-MM.
Até 12 faturas; erro amigável se exceder.
Mostrar saldo de créditos após login//me.
Em /calculator: uso de React Hook Form + Zod (tipos claros) + feedback de erro.
Estado & HTTP

React Query (cache, loading, error).
Axios com interceptor para Authorization.
Tratamento de 401: redirecionar login e limpar sessão.
Acessibilidade/UX

Contraste com slateDark/textLight em cabeçalhos/rodapés.
Estados de foco/hover (primaryHover).
Mensagens de erro acessíveis (aria-live).
Componentes Base

Button (variants: primary, secondary, subtle; loading).
Input (label, helper, error).
Card (header, body).
Badge (para status de pagamento).
Alert (success/error/info).
Skeleton (loading de listas e cards).
Exemplos de Requisição (resumido)

Login (form URL-encoded): username=<email>&password=<senha>
Calcular: body JSON acima; header com Bearer.
Create order: sem body extra (usa pacote padrão de 3 créditos no backend hoje).
Erros e Mensagens

400/422: mensagens de validação (mostrar toast/inline).
402 no cálculo: “Créditos insuficientes” (CTA para /credits).
404 SELIC/IPCA: sugerir tentar novamente mais tarde (dados econômicos ausentes).
Integração com Domínios

Produção:
API: https://calculaconfia-production.up.railway.app
Web: https://calculaconfia-web.up.railway.app (ou domínio)
Ajustar FRONTEND_URL/PUBLIC_BASE_URL no backend conforme domínio final.
Checklist de Pronto-Para-Deploy

.env.local com NEXT_PUBLIC_API_URL (dev).
Railway (frontend): NEXT_PUBLIC_API_URL de produção.
CORS ok (backend permite o domínio do frontend).
Páginas de retorno do MP publicadas.
Smoke test: login → /me; /credits → redirect; /calculator → resultado.