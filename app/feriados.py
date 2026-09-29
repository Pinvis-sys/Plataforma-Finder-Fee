"""Feriados nacionais do Brasil, para preencher o calendário de dias úteis."""
from __future__ import annotations

from datetime import date, timedelta


def easter(year: int) -> date:
    """Domingo de Páscoa (calendário gregoriano, algoritmo de Meeus/Jones/Butcher)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741 - nome do algoritmo
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def national_holidays(year: int, optional: bool = False) -> list[tuple[date, str]]:
    """Feriados nacionais (Leis 662/1949, 6.802/1980 e 14.759/2023). Com `optional`, inclui os pontos
    facultativos em que o comércio costuma parar: Carnaval (segunda e terça) e Corpus Christi.
    Feriados estaduais e municipais variam por local e são cadastrados à parte."""
    p = easter(year)
    out = [
        (date(year, 1, 1), "Confraternização Universal"),
        (p - timedelta(days=2), "Sexta-feira Santa"),
        (date(year, 4, 21), "Tiradentes"),
        (date(year, 5, 1), "Dia do Trabalho"),
        (date(year, 9, 7), "Independência do Brasil"),
        (date(year, 10, 12), "Nossa Senhora Aparecida"),
        (date(year, 11, 2), "Finados"),
        (date(year, 11, 15), "Proclamação da República"),
        (date(year, 12, 25), "Natal"),
    ]
    if year >= 2024:
        out.append((date(year, 11, 20), "Dia Nacional de Zumbi e da Consciência Negra"))
    if optional:
        out += [
            (p - timedelta(days=48), "Carnaval (ponto facultativo)"),
            (p - timedelta(days=47), "Carnaval (ponto facultativo)"),
            (p + timedelta(days=60), "Corpus Christi (ponto facultativo)"),
        ]
    return sorted(out)
