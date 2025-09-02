import asyncio
import sys
import os

# Adicionar o diretório raiz ao path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from app.core.database import engine, Base
from app.models_schemas.models import * # Importa todos os modelos

async def main():
    print("Iniciando a criação de todas as tabelas...")
    async with engine.begin() as conn:
        # Apaga tudo que existe (garantia extra)
        await conn.run_sync(Base.metadata.drop_all)
        # Cria tudo do zero
        await conn.run_sync(Base.metadata.create_all)
    print("✅ Tabelas criadas com sucesso!")

if __name__ == "__main__":
    asyncio.run(main())