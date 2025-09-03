import mercadopago
from datetime import datetime, timedelta
from ..core.config import settings

sdk = mercadopago.SDK(settings.MERCADO_PAGO_ACCESS_TOKEN)

def create_pix_payment(user_id: int, amount: float, description: str):
    expiration_time = datetime.utcnow() + timedelta(minutes=30)
    payment_data = {
        "transaction_amount": amount,
        "description": description,
        "payment_method_id": "pix",
        "payer": {
            "email": f"user-{user_id}@calculaconfia.com", # Email fictício, exigido pelo MP
        },
        "external_reference": str(user_id), # Referência para sabermos quem pagou
        "date_of_expiration": expiration_time.strftime("%Y-%m-%dT%H:%M:%S.000-03:00")
    }
    payment_response = sdk.payment().create(payment_data)
    payment = payment_response["response"]

    if payment["status"] == "pending":
        qr_code = payment["point_of_interaction"]["transaction_data"]["qr_code"]
        qr_code_base64 = payment["point_of_interaction"]["transaction_data"]["qr_code_base64"]
        return {"qr_code": qr_code, "qr_code_base64": qr_code_base64}
    else:
        raise Exception("Failed to create PIX payment")