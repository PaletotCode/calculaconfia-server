from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Dict, Iterable, List, Set, Tuple
from dateutil.relativedelta import relativedelta


PIS_COFINS_FACTOR = Decimal("0.037955")


def month_start(d: date) -> date:
    return d.replace(day=1)


def month_range(start: date, end: date) -> List[date]:
    """Inclusive list of month-start dates from start..end (start <= end)."""
    start = month_start(start)
    end = month_start(end)
    months: List[date] = []
    current = start
    while current <= end:
        months.append(current)
        current = month_start(current + relativedelta(months=1))
    return months


def build_icms_series_from_ipca(
    mean_icms: Decimal,
    months: List[date],
    ipca_rates: Dict[date, Decimal],
) -> Dict[date, Decimal]:
    """
    Reconstrói a série mensal do ICMS a partir do valor médio no primeiro mês (ancora),
    aplicando o IPCA mês a mês para frente.

    Regra do exemplo: para sair de Ago->Set, usar IPCA de Set/2015.
    Assim, valor[mes] = valor[mes_anterior] * (1 + ipca_rate[mes]).
    """
    if not months:
        return {}

    series: Dict[date, Decimal] = {}
    series[months[0]] = mean_icms

    for prev, cur in zip(months, months[1:]):
        r = ipca_rates.get(cur, Decimal("0"))
        series[cur] = series[prev] * (Decimal("1") + r)

    return series


def cumulative_selic_factors(
    months: List[date],
    selic_rates: Dict[date, Decimal],
) -> Dict[date, Decimal]:
    """
    Fatores cumulativos da SELIC para atualizar valores até o mês final (months[-1]).
    Fator do último mês = 1.0.
    Para mês m, fator = ∏_{k=m+1..end} (1 + selic[k]). Implementado de trás para frente.
    """
    if not months:
        return {}
    factors: Dict[date, Decimal] = {months[-1]: Decimal("1")}
    # Caminhar de trás pra frente, usando a taxa do mês seguinte
    for i in range(len(months) - 2, -1, -1):
        nxt = months[i + 1]
        rate_next = selic_rates.get(nxt, Decimal("0"))
        factors[months[i]] = factors[nxt] * (Decimal("1") + rate_next)
    return factors


def compute_total_refund(
    provided_icms: Dict[date, Decimal],
    most_recent: date,
    ipca_rates: Dict[date, Decimal],
    selic_rates: Dict[date, Decimal],
) -> Tuple[Decimal, Dict[date, Dict[str, Decimal]]]:
    """
    Calcula o total a restituir segundo a nova especificação.

    - Período: 120 meses terminando em `most_recent`.
    - ICMS_BASE = média dos ICMS informados pelo usuário.
    - Reconstrução com IPCA: ancora ICMS_BASE no primeiro mês do período e
      aplica IPCA mês a mês para frente. Meses informados são sobrescritos
      com os valores reais.
    - Indevido mensal = ICMS_mês * 3,7955%.
    - Atualização pela SELIC: aplicar fator cumulativo até o último mês.
      EXCEÇÃO: meses informados (reais) não recebem correção SELIC (fator 1.0).

    Retorna (total_decimal, breakdown_por_mes).
    """
    if not provided_icms:
        return Decimal("0"), {}

    # Determina período de 120 meses
    start = month_start(most_recent + relativedelta(months=-119))
    months = month_range(start, most_recent)

    # Média dos ICMS informados
    mean_icms = sum(provided_icms.values()) / Decimal(len(provided_icms))

    # Reconstrução ICMS com IPCA
    icms_series = build_icms_series_from_ipca(mean_icms, months, ipca_rates)

    # Sobrescrever com valores reais fornecidos
    for d, v in provided_icms.items():
        d = month_start(d)
        if d in icms_series:
            icms_series[d] = v

    # Fatores cumulativos da SELIC
    selic_factors = cumulative_selic_factors(months, selic_rates)

    # Cálculo do indevido e aplicação de SELIC
    total = Decimal("0")
    breakdown: Dict[date, Dict[str, Decimal]] = {}
    provided_months: Set[date] = set(map(month_start, provided_icms.keys()))

    for d in months:
        icms_value = icms_series.get(d, Decimal("0"))
        indevido = icms_value * PIS_COFINS_FACTOR
        # Se mês foi informado, não aplicar SELIC
        fator = Decimal("1") if d in provided_months else selic_factors.get(d, Decimal("1"))
        corrigido = indevido * fator
        total += corrigido

        breakdown[d] = {
            "icms": icms_value,
            "indevido": indevido,
            "fator_selic": fator,
            "corrigido": corrigido,
        }

    return total, breakdown

