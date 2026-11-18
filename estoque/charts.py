"""Gráficos SVG próprios (sem bibliotecas externas). Cores vêm de variáveis CSS (--c1..--c8)."""
import math

from django.utils.html import escape
from django.utils.safestring import mark_safe


def compacto(v):
    v = float(v)
    if abs(v) >= 1_000_000:
        return f"{v / 1_000_000:.1f} mi".replace(".", ",")
    if abs(v) >= 1_000:
        return f"{v / 1_000:.0f} mil"
    return f"{v:.0f}"


def _teto(maximo):
    """Teto do eixo cujo quarto é um número 'redondo' (ticks legíveis)."""
    if maximo <= 0:
        return 4
    if maximo < 40:
        return math.ceil(maximo / 4) * 4
    exp = 10 ** math.floor(math.log10(maximo))
    for m in (1.2, 2, 2.4, 4, 6, 8, 10, 12):
        if maximo <= m * exp:
            return m * exp
    return 10 * exp


def barras_duplas(rotulos, serie_a, serie_b, nome_a, nome_b, descricao):
    W, H, L, R, T, B = 640, 270, 46, 10, 14, 34
    valores = [float(x) for x in list(serie_a) + list(serie_b)]
    teto = _teto(max(valores) if valores else 0)
    n = max(len(rotulos), 1)
    gw = (W - L - R) / n
    bw = min(gw * 0.3, 34)
    h_util = H - T - B
    out = [f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" aria-label="{escape(descricao)}" preserveAspectRatio="xMidYMid meet">']
    for i in range(5):
        y = T + h_util * i / 4
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>')
        out.append(f'<text x="{L - 6}" y="{y + 4:.1f}" text-anchor="end" class="ax">{compacto(teto * (4 - i) / 4)}</text>')
    for i, rot in enumerate(rotulos):
        cx = L + gw * i + gw / 2
        for k, (serie, nome, cor) in enumerate(((serie_a, nome_a, "var(--c1)"), (serie_b, nome_b, "var(--c2)"))):
            v = float(serie[i])
            h = h_util * v / teto
            x = cx - bw - 1.5 if k == 0 else cx + 1.5
            out.append(
                f'<rect x="{x:.1f}" y="{T + h_util - h:.1f}" width="{bw:.1f}" height="{max(h, 0):.1f}" rx="3" fill="{cor}">'
                f'<title>{escape(rot)} · {escape(nome)}: R$ {v:,.0f}</title></rect>'.replace(",", ".")
            )
        out.append(f'<text x="{cx:.1f}" y="{H - 12}" text-anchor="middle" class="ax">{escape(rot)}</text>')
    out.append("</svg>")
    return mark_safe("".join(out))


def area(rotulos, valores, descricao, altura=220):
    W, H, L, R, T, B = 640, altura, 46, 12, 14, 30
    vals = [float(v) for v in valores]
    if not vals:
        return ""
    teto = _teto(max(vals))
    piso = 0
    h_util = H - T - B
    n = len(vals)
    passo = (W - L - R) / max(n - 1, 1)
    pts = [(L + passo * i, T + h_util - h_util * (v - piso) / (teto - piso)) for i, v in enumerate(vals)]
    out = [f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" aria-label="{escape(descricao)}">']
    for i in range(5):
        y = T + h_util * i / 4
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>')
        out.append(f'<text x="{L - 6}" y="{y + 4:.1f}" text-anchor="end" class="ax">{compacto(teto * (4 - i) / 4)}</text>')
    linha = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    base = T + h_util
    out.append(f'<polygon points="{L},{base} {linha} {pts[-1][0]:.1f},{base}" fill="var(--c1)" opacity=".14"/>')
    out.append(f'<polyline points="{linha}" fill="none" stroke="var(--c1)" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>')
    salto = max(1, math.ceil(n / 8))
    for i, (x, y) in enumerate(pts):
        if n <= 30:
            out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="var(--surface)" stroke="var(--c1)" stroke-width="2"><title>{escape(rotulos[i])}: {vals[i]:,.0f}</title></circle>'.replace(",", "."))
        if (i % salto == 0 and (n - 1 - i >= salto * 0.6 or i == n - 1)) or i == n - 1:
            out.append(f'<text x="{x:.1f}" y="{H - 10}" text-anchor="middle" class="ax">{escape(rotulos[i])}</text>')
    out.append("</svg>")
    return mark_safe("".join(out))


def rosca(itens, descricao):
    """itens: [(rótulo, valor)] -> (svg, legenda)."""
    total = sum(float(v) for _, v in itens) or 1
    r, c = 70, 2 * math.pi * 70
    out = [f'<svg viewBox="0 0 200 200" class="donut" role="img" aria-label="{escape(descricao)}">',
           '<circle cx="100" cy="100" r="70" fill="none" stroke="var(--surface-2)" stroke-width="28"/>']
    legenda, acum = [], 0.0
    for i, (rot, v) in enumerate(itens):
        v = float(v)
        frac = v / total
        cor = f"var(--c{i % 8 + 1})"
        out.append(
            f'<circle cx="100" cy="100" r="{r}" fill="none" stroke="{cor}" stroke-width="28" '
            f'stroke-dasharray="{max(frac * c - 1.5, 0):.2f} {c:.2f}" stroke-dashoffset="{-acum * c:.2f}" '
            f'transform="rotate(-90 100 100)"><title>{escape(rot)}: {frac * 100:.1f}%</title></circle>'
        )
        acum += frac
        legenda.append({"rotulo": rot, "valor": v, "pct": frac * 100, "cor": cor})
    out.append(f'<text x="100" y="97" text-anchor="middle" class="donut-v">R$ {compacto(total)}</text>')
    out.append('<text x="100" y="116" text-anchor="middle" class="ax">em estoque</text></svg>')
    return mark_safe("".join(out)), legenda
