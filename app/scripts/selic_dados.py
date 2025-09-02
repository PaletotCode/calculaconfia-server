import requests
import pandas as pd

url = 'https://api.bcb.gov.br/dados/serie/bcdata.sgs.11/dados?formato=csv'  # Exemplo de endpoint
r = requests.get(url)
r.raise_for_status()

df = pd.read_csv(pd.compat.StringIO(r.text), sep=';')
df['data'] = pd.to_datetime(df['data'], dayfirst=True)
df['ano'] = df['data'].dt.year
df_ult10 = df[df['ano'] >= df['ano'].max() - 9]  # últimos 10 anos

print(df_ult10[['data', 'valor']])
