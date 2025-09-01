🚀 CalculaConfia (Torres Project)
API de backend comercial para uma calculadora de estimativa de restituição de PIS/Cofins sobre o ICMS em faturas de energia.

🎯 Objetivo do Projeto
O objetivo desta aplicação é fornecer uma API robusta e escalável para uma calculadora web. A ferramenta permite que consumidores de energia elétrica estimem o valor aproximado da restituição referente à cobrança indevida de PIS/Cofins na base de cálculo do ICMS, conforme decisões judiciais. A plataforma gerencia usuários, créditos de uso e armazena um histórico detalhado dos cálculos realizados.

✨ Stacks de Tecnologia
A aplicação foi construída com um conjunto de tecnologias modernas focadas em performance, segurança e escalabilidade.

Backend
Python 3.11

FastAPI: Framework web assíncrono para a construção da API.

Uvicorn: Servidor ASGI para rodar a aplicação FastAPI.

Pydantic: Para validação de dados, configurações e serialização.

Banco de Dados
PostgreSQL 15: Banco de dados relacional para armazenamento persistente dos dados.

SQLAlchemy: ORM para interação com o banco de dados de forma assíncrona.

Alembic: Ferramenta para gerenciar as migrações (evolução do esquema) do banco de dados.

Infraestrutura e Serviços Adicionais
Docker & Docker Compose: Para containerização e orquestração de todo o ambiente de desenvolvimento.

Redis: Banco de dados em memória de alta velocidade, utilizado para cache de requisições e como message broker.

Celery: Sistema de filas para execução de tarefas em background (como envio de e-mails), garantindo que a API permaneça rápida para o usuário.

⚙️ Como Executar o Projeto
Siga os passos abaixo para iniciar o ambiente de desenvolvimento localmente.

1. Pré-requisitos
Docker Desktop instalado e em execução.

2. Configuração
O projeto utiliza variáveis de ambiente para configuração. Para rodar com Docker, elas já estão pré-configuradas no arquivo docker-compose.yml. Para rodar localmente sem Docker, crie um arquivo .env na raiz do projeto a partir do exemplo abaixo.

Arquivo .env.example:

Code snippet

# Configuração do Banco de Dados
DATABASE_URL="postgresql+asyncpg://torres_user:torres_password@localhost:5432/torres_db"

# Chave secreta para JWT - Gere uma nova com 'openssl rand -hex 32'
SECRET_KEY="change-this-super-secret-key-in-production-please"

# Configuração do Redis e Celery
REDIS_URL="redis://localhost:6379/0"
CELERY_BROKER_URL="redis://localhost:6379/1"
CELERY_RESULT_BACKEND="redis://localhost:6379/2"

# Configuração de E-mail (substitua com suas credenciais)
MAIL_USERNAME="seu_email@gmail.com"
MAIL_PASSWORD="sua_senha_de_app_do_gmail"
MAIL_FROM="seu_email@gmail.com"
MAIL_SERVER="smtp.gmail.com"
3. Execução com Docker
Subir os Containers:
Abra um terminal na raiz do projeto e execute:

Bash

docker compose up --build
Este comando irá construir as imagens e iniciar todos os serviços (API, Postgres, Redis, etc.).

Aplicar as Migrações do Banco:
Com os containers rodando, abra um novo terminal e execute o comando abaixo para criar as tabelas no banco de dados:

Bash

docker compose exec api alembic upgrade head
Neste ponto, a API estará rodando em http://localhost:8000.

📁 Estrutura do Projeto
A aplicação segue uma arquitetura modular para facilitar a manutenção e o desenvolvimento.

/
├── app/                  # Contém todo o código fonte da aplicação
│   ├── api/              # Endpoints da API (rotas)
│   ├── core/             # Lógica central (config, db, security, etc.)
│   ├── models_schemas/   # Modelos do DB (SQLAlchemy) e Schemas de dados (Pydantic)
│   ├── services/         # Lógica de negócio principal
│   ├── scripts/          # Scripts de gerenciamento (criar admin, etc.)
│   └── main.py           # Ponto de entrada da aplicação FastAPI
├── alembic/              # Arquivos de migração do Alembic
├── docker-compose.yml    # Orquestração dos containers
├── Dockerfile            # Receita para construir a imagem da aplicação
└── requirements.txt      # Dependências Python
🌐 Endpoints Principais da API
Todos os endpoints são prefixados com /api/v1.

Método	Endpoint	Protegido	Descrição
POST	/register	Não	Registra um novo usuário.
POST	/login	Não	Autentica um usuário e retorna um token JWT.
POST	/calcular	Sim	Executa um cálculo e consome um crédito.
GET	/historico	Sim	Retorna o histórico de cálculos do usuário.
GET	/me	Sim	Retorna as informações do usuário logado.
