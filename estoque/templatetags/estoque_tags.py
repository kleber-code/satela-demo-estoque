from decimal import Decimal

from django import template
from django.utils.safestring import mark_safe

register = template.Library()

SITUACOES = {
    "ok": ("Normal", "ok"),
    "minimo": ("Abaixo do mínimo", "warn"),
    "ruptura": ("Ruptura", "danger"),
    "sem_giro": ("Sem giro", "info"),
}


def _milhar(n, casas=0):
    n = Decimal(n or 0)
    txt = f"{n:,.{casas}f}"
    return txt.replace(",", "§").replace(".", ",").replace("§", ".")


@register.filter
def brl(valor):
    try:
        return "R$ " + _milhar(valor, 2)
    except Exception:
        return "R$ 0,00"


@register.filter
def milhar(valor):
    try:
        return _milhar(valor, 0)
    except Exception:
        return "0"


@register.filter
def pct(valor):
    try:
        return _milhar(valor, 1) + "%"
    except Exception:
        return "0%"


@register.filter
def absoluto(valor):
    return abs(valor or 0)


@register.filter
def sinal(valor):
    return f"+{valor}" if valor and valor > 0 else str(valor)


@register.simple_tag
def badge_situacao(produto):
    rotulo, cls = SITUACOES.get(produto.situacao, SITUACOES["ok"])
    return mark_safe(f'<span class="badge b-{cls}">{rotulo}</span>')


@register.filter
def situacao_rotulo(chave):
    return SITUACOES.get(chave, SITUACOES["ok"])[0]


@register.simple_tag(takes_context=True)
def url_com(context, **kwargs):
    """Querystring atual com parâmetros substituídos (remove 'page')."""
    q = context["request"].GET.copy()
    q.pop("page", None)
    for k, v in kwargs.items():
        if v in (None, ""):
            q.pop(k, None)
        else:
            q[k] = v
    s = q.urlencode()
    return "?" + s if s else "?"
