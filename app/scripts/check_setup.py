import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

async def check_setup():
    """Verifica se o projeto está configurado corretamente"""
    print("🔍 Verificando configuração do Torres Project...")
    
    checks = []
    
    # 1. Verificar imports
    try:
        from app.core.security import get_password_hash, verify_password
        checks.append("✅ Security module OK")
    except ImportError as e:
        checks.append(f"❌ Security module ERROR: {e}")
    
    # 2. Verificar configurações
    try:
        from app.core.config import settings
        if settings.SECRET_KEY == "change-this-super-secret-key-in-production-please":
            checks.append("⚠️  Using default SECRET_KEY (change for production)")
        else:
            checks.append("✅ SECRET_KEY configured")
    except Exception as e:
        checks.append(f"❌ Config ERROR: {e}")
    
    # 3. Verificar database
    try:
        from app.core.database import SessionLocal
        async with SessionLocal() as db:
            await db.execute("SELECT 1")
        checks.append("✅ Database connection OK")
    except Exception as e:
        checks.append(f"❌ Database ERROR: {e}")
    
    # 4. Verificar models
    try:
        from app.models_schemas.models import User, UserPlan
        checks.append("✅ Models import OK")
    except Exception as e:
        checks.append(f"❌ Models ERROR: {e}")
    
    print("\n📋 Resultados:")
    for check in checks:
        print(f"  {check}")
    
    errors = [c for c in checks if "❌" in c]
    warnings = [c for c in checks if "⚠️" in c]
    
    if errors:
        print(f"\n🚨 {len(errors)} erro(s) crítico(s) encontrado(s)")
        return False
    elif warnings:
        print(f"\n⚠️  {len(warnings)} aviso(s) encontrado(s)")
        return True
    else:
        print("\n✅ Projeto configurado corretamente!")
        return True

if __name__ == "__main__":
    result = asyncio.run(check_setup())
    sys.exit(0 if result else 1)