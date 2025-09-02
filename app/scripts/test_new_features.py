#!/usr/bin/env python3
"""
Script de teste para as novas funcionalidades implementadas
Testa:
1. Registro com telefone
2. Verificação de conta
3. Sistema de referência
4. Autenticação por telefone
5. Cálculos com nova lógica de créditos
6. Reset de senha
"""

import asyncio
import requests
import json
import sys
import os
from datetime import datetime

# Configurações
BASE_URL = "http://localhost:8000/api/v1"
HEADERS = {"Content-Type": "application/json"}

# Dados de teste
TEST_USERS = [
    {
        "phone_number": "11999887766",
        "password": "minhasenha123A",
        "email": "usuario1@teste.com",
        "first_name": "João",
        "last_name": "Silva"
    },
    {
        "phone_number": "11888776655", 
        "password": "outrasenha456B",
        "email": "usuario2@teste.com",
        "first_name": "Maria",
        "last_name": "Santos"
    }
]


def log(message: str, status: str = "INFO"):
    """Log formatado"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {status}: {message}")


def test_api_health():
    """Testa se a API está respondendo"""
    try:
        response = requests.get(f"{BASE_URL}/health")
        if response.status_code == 200:
            log("✅ API Health Check OK", "SUCCESS")
            return True
        else:
            log(f"❌ API Health Check Failed: {response.status_code}", "ERROR")
            return False
    except Exception as e:
        log(f"❌ API não está respondendo: {e}", "ERROR")
        return False


def register_user(user_data, referral_code=None):
    """Testa registro de usuário"""
    log(f"Registrando usuário: {user_data['phone_number']}")
    
    payload = user_data.copy()
    if referral_code:
        payload["applied_referral_code"] = referral_code
        log(f"  Usando código de referência: {referral_code}")
    
    try:
        response = requests.post(f"{BASE_URL}/register", json=payload, headers=HEADERS)
        
        if response.status_code == 201:
            data = response.json()
            log(f"✅ Usuário registrado com sucesso", "SUCCESS")
            log(f"  ID: {data['id']}")
            log(f"  Telefone: {data['phone_number']}")
            log(f"  Código de referência: {data['referral_code']}")
            log(f"  Verificado: {data['is_verified']}")
            log(f"  Ativo: {data['is_active']}")
            return data
        else:
            log(f"❌ Erro no registro: {response.status_code} - {response.text}", "ERROR")
            return None
            
    except Exception as e:
        log(f"❌ Erro de conexão no registro: {e}", "ERROR")
        return None


def send_verification_code(identifier):
    """Testa envio de código de verificação"""
    log(f"Enviando código de verificação para: {identifier}")
    
    payload = {"identifier": identifier}
    
    try:
        response = requests.post(f"{BASE_URL}/auth/send-verification-code", json=payload, headers=HEADERS)
        
        if response.status_code == 200:
            data = response.json()
            log(f"✅ Código de verificação enviado", "SUCCESS")
            log(f"  Expira em: {data['expires_in_minutes']} minutos")
            return True
        else:
            log(f"❌ Erro no envio do código: {response.status_code} - {response.text}", "ERROR")
            return False
            
    except Exception as e:
        log(f"❌ Erro de conexão no envio do código: {e}", "ERROR")
        return False


def verify_account(identifier, code):
    """Testa verificação de conta"""
    log(f"Verificando conta: {identifier} com código: {code}")
    
    payload = {"identifier": identifier, "code": code}
    
    try:
        response = requests.post(f"{BASE_URL}/auth/verify-account", json=payload, headers=HEADERS)
        
        if response.status_code == 200:
            data = response.json()
            log(f"✅ Conta verificada com sucesso", "SUCCESS")
            log(f"  Verificado: {data['is_verified']}")
            log(f"  Ativo: {data['is_active']}")
            log(f"  Créditos: {data['credits']}")
            return data
        else:
            log(f"❌ Erro na verificação: {response.status_code} - {response.text}", "ERROR")
            return None
            
    except Exception as e:
        log(f"❌ Erro de conexão na verificação: {e}", "ERROR")
        return None


def login_user(identifier, password):
    """Testa login do usuário"""
    log(f"Fazendo login: {identifier}")
    
    payload = {
        "username": identifier,
        "password": password
    }
    
    try:
        response = requests.post(f"{BASE_URL}/login", data=payload)
        
        if response.status_code == 200:
            data = response.json()
            log(f"✅ Login realizado com sucesso", "SUCCESS")
            log(f"  Token type: {data['token_type']}")
            log(f"  Expira em: {data['expires_in']} segundos")
            return data["access_token"]
        else:
            log(f"❌ Erro no login: {response.status_code} - {response.text}", "ERROR")
            return None
            
    except Exception as e:
        log(f"❌ Erro de conexão no login: {e}", "ERROR")
        return None


def test_calculation(token):
    """Testa cálculo com o token"""
    log("Testando cálculo")
    
    headers = {
        **HEADERS,
        "Authorization": f"Bearer {token}"
    }
    
    payload = {
        "bills": [
            {"icms_value": 150.50, "issue_date": "2024-01"},
            {"icms_value": 165.75, "issue_date": "2024-02"},
            {"icms_value": 140.25, "issue_date": "2024-03"}
        ]
    }
    
    try:
        response = requests.post(f"{BASE_URL}/calcular", json=payload, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            log(f"✅ Cálculo realizado com sucesso", "SUCCESS")
            log(f"  Valor calculado: R$ {data['valor_calculado']:,.2f}")
            log(f"  Créditos restantes: {data['creditos_restantes']}")
            log(f"  Tempo de processamento: {data['processing_time_ms']}ms")
            return data
        else:
            log(f"❌ Erro no cálculo: {response.status_code} - {response.text}", "ERROR")
            return None
            
    except Exception as e:
        log(f"❌ Erro de conexão no cálculo: {e}", "ERROR")
        return None


def get_user_info(token):
    """Testa busca de informações do usuário"""
    log("Buscando informações do usuário")
    
    headers = {
        **HEADERS,
        "Authorization": f"Bearer {token}"
    }
    
    try:
        response = requests.get(f"{BASE_URL}/me", headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            log(f"✅ Informações do usuário obtidas", "SUCCESS")
            log(f"  ID: {data['id']}")
            log(f"  Telefone: {data['phone_number']}")
            log(f"  Email: {data.get('email', 'Não informado')}")
            log(f"  Nome: {data.get('first_name', '')} {data.get('last_name', '')}")
            log(f"  Código de referência: {data['referral_code']}")
            log(f"  Créditos: {data['credits']}")
            return data
        else:
            log(f"❌ Erro ao buscar informações: {response.status_code} - {response.text}", "ERROR")
            return None
            
    except Exception as e:
        log(f"❌ Erro de conexão ao buscar informações: {e}", "ERROR")
        return None


def get_referral_stats(token):
    """Testa estatísticas de referência"""
    log("Buscando estatísticas de referência")
    
    headers = {
        **HEADERS,
        "Authorization": f"Bearer {token}"
    }
    
    try:
        response = requests.get(f"{BASE_URL}/referral/stats", headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            log(f"✅ Estatísticas de referência obtidas", "SUCCESS")
            log(f"  Código de referência: {data['referral_code']}")
            log(f"  Total de indicações: {data['total_referrals']}")
            log(f"  Créditos ganhos: {data['referral_credits_earned']}")
            log(f"  Créditos restantes: {data['referral_credits_remaining']}")
            return data
        else:
            log(f"❌ Erro ao buscar estatísticas de referência: {response.status_code} - {response.text}", "ERROR")
            return None
            
    except Exception as e:
        log(f"❌ Erro de conexão ao buscar estatísticas: {e}", "ERROR")
        return None


def get_credit_balance(token):
    """Testa saldo de créditos válidos"""
    log("Buscando saldo de créditos válidos")
    
    headers = {
        **HEADERS,
        "Authorization": f"Bearer {token}"
    }
    
    try:
        response = requests.get(f"{BASE_URL}/credits/balance", headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            log(f"✅ Saldo de créditos obtido", "SUCCESS")
            log(f"  Créditos válidos: {data['valid_credits']}")
            log(f"  Créditos legado: {data['legacy_credits']}")
            return data
        else:
            log(f"❌ Erro ao buscar saldo de créditos: {response.status_code} - {response.text}", "ERROR")
            return None
            
    except Exception as e:
        log(f"❌ Erro de conexão ao buscar saldo: {e}", "ERROR")
        return None


def simulate_referral_payment(token):
    """Testa simulação de pagamento para bônus de referência"""
    log("Simulando pagamento para testar bônus de referência")
    
    headers = {
        **HEADERS,
        "Authorization": f"Bearer {token}"
    }
    
    try:
        response = requests.post(f"{BASE_URL}/dev/simulate-referral-payment", headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            log(f"✅ Pagamento simulado com sucesso", "SUCCESS")
            log(f"  Créditos adicionados: {data['credits_added']}")
            log(f"  Novo saldo: {data['new_balance']}")
            return data
        else:
            log(f"❌ Erro na simulação de pagamento: {response.status_code} - {response.text}", "ERROR")
            return None
            
    except Exception as e:
        log(f"❌ Erro de conexão na simulação: {e}", "ERROR")
        return None


def request_password_reset(email):
    """Testa solicitação de reset de senha"""
    log(f"Solicitando reset de senha para: {email}")
    
    payload = {"email": email}
    
    try:
        response = requests.post(f"{BASE_URL}/auth/request-password-reset", json=payload, headers=HEADERS)
        
        if response.status_code == 200:
            data = response.json()
            log(f"✅ Reset de senha solicitado", "SUCCESS")
            log(f"  Mensagem: {data['message']}")
            return True
        else:
            log(f"❌ Erro na solicitação de reset: {response.status_code} - {response.text}", "ERROR")
            return False
            
    except Exception as e:
        log(f"❌ Erro de conexão no reset: {e}", "ERROR")
        return False


def reset_password(email, code, new_password):
    """Testa reset de senha com código"""
    log(f"Resetando senha para: {email} com código: {code}")
    
    payload = {
        "email": email,
        "code": code,
        "new_password": new_password
    }
    
    try:
        response = requests.post(f"{BASE_URL}/auth/reset-password", json=payload, headers=HEADERS)
        
        if response.status_code == 200:
            data = response.json()
            log(f"✅ Senha resetada com sucesso", "SUCCESS")
            log(f"  Mensagem: {data['message']}")
            return True
        else:
            log(f"❌ Erro no reset de senha: {response.status_code} - {response.text}", "ERROR")
            return False
            
    except Exception as e:
        log(f"❌ Erro de conexão no reset de senha: {e}", "ERROR")
        return False


def main():
    """Função principal de teste"""
    print("="*60)
    print("🧪 TESTE DAS NOVAS FUNCIONALIDADES - CALCULACONFIA")
    print("="*60)
    
    # 1. Verificar se API está rodando
    if not test_api_health():
        log("API não está respondendo. Verifique se está rodando.", "FATAL")
        sys.exit(1)
    
    print("\n" + "="*60)
    print("📱 TESTE 1: REGISTRO E VERIFICAÇÃO POR TELEFONE")
    print("="*60)
    
    # 2. Registrar primeiro usuário
    user1_data = register_user(TEST_USERS[0])
    if not user1_data:
        log("Falha no registro do usuário 1", "FATAL")
        return
    
    # 3. Enviar código de verificação
    if not send_verification_code(TEST_USERS[0]["phone_number"]):
        log("Falha no envio do código de verificação", "FATAL")
        return
    
    # 4. Simular verificação (código fixo para teste)
    verification_code = input("\n🔢 Digite o código de verificação mostrado no console: ")
    
    verified_user = verify_account(TEST_USERS[0]["phone_number"], verification_code)
    if not verified_user:
        log("Falha na verificação da conta", "FATAL")
        return
    
    print("\n" + "="*60)
    print("🔑 TESTE 2: AUTENTICAÇÃO POR TELEFONE")
    print("="*60)
    
    # 5. Fazer login com telefone
    token1 = login_user(TEST_USERS[0]["phone_number"], TEST_USERS[0]["password"])
    if not token1:
        log("Falha no login com telefone", "FATAL")
        return
    
    # 6. Buscar informações do usuário
    user_info = get_user_info(token1)
    if not user_info:
        log("Falha ao buscar informações do usuário", "ERROR")
    
    print("\n" + "="*60)
    print("👥 TESTE 3: SISTEMA DE REFERÊNCIA")
    print("="*60)
    
    # 7. Registrar segundo usuário com código de referência
    referral_code = user_info.get("referral_code") if user_info else None
    if referral_code:
        user2_data = register_user(TEST_USERS[1], referral_code)
        if user2_data:
            # Verificar segundo usuário
            if send_verification_code(TEST_USERS[1]["phone_number"]):
                verification_code2 = input("\n🔢 Digite o código de verificação para o segundo usuário: ")
                verify_account(TEST_USERS[1]["phone_number"], verification_code2)
                
                # Login do segundo usuário
                token2 = login_user(TEST_USERS[1]["phone_number"], TEST_USERS[1]["password"])
                if token2:
                    # Simular pagamento para ativar bônus de referência
                    simulate_referral_payment(token2)
    
    # 8. Verificar estatísticas de referência do primeiro usuário
    get_referral_stats(token1)
    
    print("\n" + "="*60)
    print("💳 TESTE 4: SISTEMA DE CRÉDITOS VÁLIDOS")
    print("="*60)
    
    # 9. Verificar saldo de créditos válidos
    get_credit_balance(token1)
    
    print("\n" + "="*60)
    print("🧮 TESTE 5: CÁLCULOS COM NOVA LÓGICA")
    print("="*60)
    
    # 10. Realizar alguns cálculos
    for i in range(2):
        log(f"Realizando cálculo {i+1}")
        calculation_result = test_calculation(token1)
        if calculation_result:
            log(f"Créditos após cálculo {i+1}: {calculation_result['creditos_restantes']}")
    
    print("\n" + "="*60)
    print("🔐 TESTE 6: RESET DE SENHA")
    print("="*60)
    
    # 11. Testar reset de senha (se usuário tem email)
    if TEST_USERS[0].get("email"):
        if request_password_reset(TEST_USERS[0]["email"]):
            reset_code = input("\n🔢 Digite o código de reset de senha enviado por email: ")
            reset_password(TEST_USERS[0]["email"], reset_code, "novaSenha789C")
    
    print("\n" + "="*60)
    print("✅ TODOS OS TESTES CONCLUÍDOS!")
    print("="*60)
    
    log("Verifique os logs da aplicação para mais detalhes", "INFO")
    log("Script de teste finalizado", "SUCCESS")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("\nTeste interrompido pelo usuário", "WARNING")
    except Exception as e:
        log(f"Erro inesperado: {e}", "FATAL")
        raise