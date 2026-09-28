"""Validação e normalização de CNPJ (dígitos verificadores)."""
import re

_W1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
_W2 = [6] + _W1


def digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _dv(base: str, weights: list[int]) -> int:
    total = sum(int(d) * w for d, w in zip(base, weights))
    rest = total % 11
    return 0 if rest < 2 else 11 - rest


def is_valid(value: str) -> bool:
    d = digits(value)
    if len(d) != 14 or d == d[0] * 14:
        return False
    d1 = _dv(d[:12], _W1)
    d2 = _dv(d[:12] + str(d1), _W2)
    return d[12:] == f"{d1}{d2}"


def normalize(value: str) -> str:
    """Retorna só os 14 dígitos (não valida)."""
    return digits(value)


def base(value: str) -> str:
    """Raiz do CNPJ (8 primeiros dígitos): identifica a empresa, sem a filial."""
    return digits(value)[:8]


def is_headquarters(value: str) -> bool:
    return digits(value)[8:12] == "0001"


def fmt(value: str) -> str:
    d = digits(value)
    if len(d) != 14:
        return value or ""
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"


def make_valid(root8: str, branch: str = "0001") -> str:
    """Gera CNPJ válido a partir da raiz (uso em testes e dados de demonstração)."""
    b = digits(root8)[:8].ljust(8, "0") + branch.rjust(4, "0")
    d1 = _dv(b, _W1)
    d2 = _dv(b + str(d1), _W2)
    return f"{b}{d1}{d2}"
