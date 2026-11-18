import csv
import json
from datetime import timedelta
from decimal import Decimal
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import TruncMonth
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import charts, services
from .forms import (
    CategoriaForm, DepositoForm, FornecedorForm, MovimentacaoForm, NovoInventarioForm, PedidoForm,
    ProdutoForm, TransferenciaForm, item_formset,
)
from .models import (
    ApiToken, Categoria, Deposito, Fornecedor, Inventario, ItemInventario, Movimentacao, Papel,
    PedidoCompra, Perfil, Produto,
)
from .services import ErroEstoque

T = Movimentacao.Tipo
MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


# ------------------------------------------------------------------ utilidades
def exige(nivel):
    """1 = leitura, 2 = operador, 3 = administrador."""
    def deco(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if request.nivel < nivel:
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return wrapper
    return deco


operador = exige(2)
administrador = exige(3)


def paginar(request, itens, por_pagina=20):
    pagina = Paginator(itens, por_pagina).get_page(request.GET.get("page"))
    q = request.GET.copy()
    q.pop("page", None)
    return pagina, q.urlencode()


def _seguro(texto):
    texto = str(texto if texto is not None else "")
    return "'" + texto if texto[:1] in ("=", "+", "-", "@", "\t", "\r") else texto


def csv_response(nome, cabecalho, linhas):
    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{nome}-{timezone.localdate():%Y%m%d}.csv"'
    resp.write("﻿")
    w = csv.writer(resp, delimiter=";")
    w.writerow(cabecalho)
    for l in linhas:
        w.writerow([_seguro(c) if isinstance(c, str) else str(c).replace(".", ",") if isinstance(c, Decimal) else c for c in l])
    return resp


def _data(valor):
    try:
        return timezone.datetime.strptime(valor, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return xff.split(",")[-1].strip() if xff else request.META.get("REMOTE_ADDR", "?")


# ------------------------------------------------------------------ saúde, login, demo
def saude(request):
    return HttpResponse("ok", content_type="text/plain")


class EntrarView(LoginView):
    template_name = "login.html"
    redirect_authenticated_user = True

    def _chaves(self, request):
        usuario = (request.POST.get("username") or "").lower()[:80]
        return [f"login:ip:{_ip(request)}", f"login:user:{usuario}"]

    def post(self, request, *args, **kwargs):
        for chave in self._chaves(request):
            if cache.get(chave, 0) >= settings.LOGIN_MAX_TENTATIVAS:
                form = self.get_form()
                form.add_error(None, "Muitas tentativas. Aguarde alguns minutos e tente novamente.")
                return self.render_to_response(self.get_context_data(form=form), status=429)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        for chave in self._chaves(self.request):
            cache.add(chave, 0, settings.LOGIN_JANELA_SEGUNDOS)
            try:
                cache.incr(chave)
            except ValueError:
                cache.set(chave, 1, settings.LOGIN_JANELA_SEGUNDOS)
        return super().form_invalid(form)

    def form_valid(self, form):
        for chave in self._chaves(self.request):
            cache.delete(chave)
        return super().form_valid(form)


@require_POST
def demo(request):
    if not settings.DEMO_MODE:
        raise Http404
    leitura = request.POST.get("papel") == "leitura"
    username, papel = ("demo.leitura", Papel.LEITURA) if leitura else ("demo", Papel.OPERADOR)
    user, criado = User.objects.get_or_create(
        username=username, defaults={"first_name": "Visitante", "last_name": "(leitura)" if leitura else "(demo)"}
    )
    if criado or user.has_usable_password():
        user.set_unusable_password()
        user.save()
    Perfil.objects.update_or_create(user=user, defaults={"papel": papel})
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return redirect("dashboard")


@require_POST
def sair(request):
    logout(request)
    return redirect("login")


# ------------------------------------------------------------------ dashboard
def _meses(n):
    hoje = timezone.localdate().replace(day=1)
    lista, ano, mes = [], hoje.year, hoje.month
    for _ in range(n):
        lista.append((ano, mes))
        mes -= 1
        if mes == 0:
            ano, mes = ano - 1, 12
    return lista[::-1]


def dashboard(request):
    agora = timezone.now()
    produtos = list(Produto.objects.filter(ativo=True).com_saldo().select_related("categoria"))
    valor_total = sum((p.valor_estoque for p in produtos), Decimal("0"))
    unidades = sum(max(p.saldo, 0) for p in produtos)
    por_situacao = {"minimo": [], "ruptura": [], "sem_giro": []}
    for p in produtos:
        s = p.situacao
        if s in por_situacao:
            por_situacao[s].append(p)
    valor_parado = sum((p.valor_estoque for p in por_situacao["sem_giro"]), Decimal("0"))

    mov = Movimentacao.objects
    expr_venda = ExpressionWrapper(-F("quantidade") * F("preco_unitario"), output_field=DecimalField(max_digits=16, decimal_places=2))
    ini30, ini60 = agora - timedelta(days=30), agora - timedelta(days=60)
    vendas = mov.filter(tipo=T.SAIDA, motivo="venda")
    v30 = vendas.filter(data__gte=ini30).aggregate(v=Sum(expr_venda))["v"] or Decimal("0")
    v60 = vendas.filter(data__gte=ini60, data__lt=ini30).aggregate(v=Sum(expr_venda))["v"] or Decimal("0")
    variacao = ((v30 - v60) / v60 * 100) if v60 else None
    n_mov30 = mov.filter(data__gte=ini30).count()

    # série mensal: compras (a custo) x vendas (a preço)
    meses = _meses(6)
    corte = timezone.make_aware(timezone.datetime(meses[0][0], meses[0][1], 1))
    expr_compra = ExpressionWrapper(F("quantidade") * F("custo_unitario"), output_field=DecimalField(max_digits=16, decimal_places=2))
    compras_m = {
        (r["m"].year, r["m"].month): r["v"]
        for r in mov.filter(tipo=T.ENTRADA, data__gte=corte).annotate(m=TruncMonth("data")).values("m").annotate(v=Sum(expr_compra))
    }
    vendas_m = {
        (r["m"].year, r["m"].month): r["v"]
        for r in vendas.filter(data__gte=corte).annotate(m=TruncMonth("data")).values("m").annotate(v=Sum(expr_venda))
    }
    rot = [MESES[m - 1] for _, m in meses]
    graf_fluxo = charts.barras_duplas(
        rot, [compras_m.get(k) or 0 for k in meses], [vendas_m.get(k) or 0 for k in meses],
        "Compras (custo)", "Vendas (preço)", "Compras a custo e vendas a preço, por mês, nos últimos 6 meses",
    )

    # valor do estoque ao longo do tempo (custo atual x saldo acumulado)
    expr_val = ExpressionWrapper(F("quantidade") * F("produto__custo"), output_field=DecimalField(max_digits=16, decimal_places=2))
    mensal = {
        (r["m"].year, r["m"].month): r["v"]
        for r in mov.annotate(m=TruncMonth("data")).values("m").annotate(v=Sum(expr_val)).order_by("m")
    }
    acum, serie = Decimal("0"), {}
    for k in sorted(mensal):
        acum += mensal[k]
        serie[k] = acum
    ult, valores = Decimal("0"), []
    for k in meses:
        ult = serie.get(k, ult) if k in serie else ult
        if k not in serie:
            anteriores = [c for c in serie if c < k]
            ult = serie[max(anteriores)] if anteriores else Decimal("0")
        valores.append(ult)
    graf_valor = charts.area(rot, valores, "Evolução do valor do estoque nos últimos 6 meses", altura=400)

    # valor por categoria
    por_cat = {}
    for p in produtos:
        por_cat[p.categoria.nome] = por_cat.get(p.categoria.nome, Decimal("0")) + max(p.valor_estoque, 0)
    ordenado = sorted(por_cat.items(), key=lambda kv: -kv[1])
    top = ordenado[:7]
    resto = sum((v for _, v in ordenado[7:]), Decimal("0"))
    if resto:
        top.append(("Outras", resto))
    graf_cat, legenda_cat = charts.rosca(top, "Distribuição do valor em estoque por categoria")

    # mais vendidos (30d)
    ranking = list(
        vendas.filter(data__gte=ini30).values("produto__nome", "produto__sku", "produto_id")
        .annotate(q=Sum("quantidade")).order_by("q")[:8]
    )
    maximo = max([-r["q"] for r in ranking], default=1)
    for r in ranking:
        r["q"] = -r["q"]
        r["pct"] = r["q"] / maximo * 100

    recentes = mov.select_related("produto", "deposito", "usuario")[:8]
    pedidos_abertos = PedidoCompra.objects.filter(status__in=["rascunho", "enviado"]).select_related("fornecedor")[:5]
    n_pedidos = PedidoCompra.objects.filter(status__in=["rascunho", "enviado"]).count()
    criticos = sorted(por_situacao["ruptura"], key=lambda p: p.nome)[:4] + sorted(
        por_situacao["minimo"], key=lambda p: (p.saldo / max(p.estoque_minimo, 1))
    )[:5]
    return render(request, "dashboard.html", {
        "valor_total": valor_total, "unidades": unidades, "n_skus": len(produtos),
        "n_minimo": len(por_situacao["minimo"]), "n_ruptura": len(por_situacao["ruptura"]),
        "n_sem_giro": len(por_situacao["sem_giro"]), "valor_parado": valor_parado,
        "v30": v30, "variacao": variacao, "n_mov30": n_mov30, "n_pedidos": n_pedidos,
        "graf_fluxo": graf_fluxo, "graf_valor": graf_valor, "graf_cat": graf_cat, "legenda_cat": legenda_cat,
        "ranking": ranking, "recentes": recentes, "pedidos_abertos": pedidos_abertos, "criticos": criticos,
        "n_depositos": Deposito.objects.filter(ativo=True).count(),
    })


# ------------------------------------------------------------------ produtos
ORDENACOES = {
    "nome": "nome", "sku": "sku", "saldo": "-saldo", "saldo_asc": "saldo",
    "valor": "-valor_estoque", "preco": "-preco",
}


def _filtrar_produtos(request):
    q = request.GET.get("q", "").strip()
    cat = request.GET.get("categoria", "")
    sit = request.GET.get("situacao", "")
    dep = request.GET.get("deposito", "")
    deposito = Deposito.objects.filter(pk=dep).first() if dep.isdigit() else None
    qs = Produto.objects.com_saldo(deposito).select_related("categoria", "fornecedor")
    if request.GET.get("inativos") != "1":
        qs = qs.filter(ativo=True)
    if q:
        qs = qs.filter(Q(nome__icontains=q) | Q(sku__icontains=q))
    if cat.isdigit():
        qs = qs.filter(categoria_id=cat)
    if sit:
        qs = qs.filtrar_situacao(sit)
    ordem = ORDENACOES.get(request.GET.get("ordem", "nome"), "nome")
    return qs.order_by(ordem, "nome"), deposito


def produto_lista(request):
    qs, deposito = _filtrar_produtos(request)
    if request.GET.get("export") == "csv":
        return csv_response(
            "produtos",
            ["SKU", "Produto", "Categoria", "Unidade", "Custo", "Preço", "Estoque mínimo", "Saldo", "Valor em estoque", "Situação"],
            [(p.sku, p.nome, p.categoria.nome, p.unidade, p.custo, p.preco, p.estoque_minimo, p.saldo,
              p.valor_estoque, services_rotulo(p.situacao)) for p in qs],
        )
    pagina, qs_str = paginar(request, qs)
    return render(request, "produtos/lista.html", {
        "pagina": pagina, "qs": qs_str, "categorias": Categoria.objects.all(), "depositos": Deposito.objects.all(),
        "deposito_sel": deposito, "total": pagina.paginator.count,
        "ordenacoes": [("nome", "Nome"), ("sku", "SKU"), ("saldo", "Maior saldo"), ("saldo_asc", "Menor saldo"),
                       ("valor", "Maior valor em estoque"), ("preco", "Maior preço")],
    })


def services_rotulo(chave):
    from .templatetags.estoque_tags import SITUACOES
    return SITUACOES[chave][0]


def produto_detalhe(request, pk):
    produto = get_object_or_404(Produto.objects.com_saldo().select_related("categoria", "fornecedor"), pk=pk)
    deposito = Deposito.objects.filter(pk=request.GET.get("deposito")).first() if request.GET.get("deposito", "").isdigit() else None
    tipo = request.GET.get("tipo") if request.GET.get("tipo") in dict(T.choices) else None
    linhas = services.kardex(produto, deposito, tipo)
    if request.GET.get("export") == "csv":
        return csv_response(
            f"kardex-{produto.sku}", ["Data", "Tipo", "Depósito", "Quantidade", "Saldo corrente", "Documento", "Observação", "Usuário"],
            [(timezone.localtime(m.data).strftime("%d/%m/%Y %H:%M"), m.get_tipo_display(), m.deposito.nome, m.quantidade,
              m.saldo_corrente, m.documento, m.observacao, m.usuario.username if m.usuario else "") for m in linhas],
        )
    pagina, qs_str = paginar(request, linhas, 15)
    por_dep = list(
        Movimentacao.objects.filter(produto=produto).values("deposito__codigo", "deposito__nome")
        .annotate(s=Sum("quantidade")).order_by("deposito__codigo")
    )
    # evolução do saldo (fim de cada dia, últimos 90 dias)
    todas = services.kardex(produto, deposito)[::-1]
    pontos = []
    if todas:
        hoje = timezone.localdate()
        ini = hoje - timedelta(days=89)
        corrente, idx = 0, 0
        por_dia = {}
        for m in todas:
            por_dia[timezone.localtime(m.data).date()] = m.saldo_corrente
        ultimo = 0
        for d in sorted(por_dia):
            if d < ini:
                ultimo = por_dia[d]
        for i in range(90):
            d = ini + timedelta(days=i)
            if d in por_dia:
                ultimo = por_dia[d]
            pontos.append((d.strftime("%d/%m"), ultimo))
        # reduz para ~30 pontos
        pontos = pontos[::3] + ([pontos[-1]] if (len(pontos) - 1) % 3 else [])
    grafico = charts.area([p[0] for p in pontos], [p[1] for p in pontos], f"Evolução do saldo de {produto.nome} nos últimos 90 dias") if pontos else ""
    return render(request, "produtos/detalhe.html", {
        "produto": produto, "pagina": pagina, "qs": qs_str, "por_dep": por_dep, "grafico": grafico,
        "depositos": Deposito.objects.all(), "deposito_sel": deposito, "tipo_sel": tipo, "tipos": T.choices,
        "cobertura": services.cobertura_dias(produto), "sugestao": services.sugestao_compra(produto),
        "total_mov": len(linhas),
    })


@operador
def produto_form(request, pk=None):
    produto = get_object_or_404(Produto, pk=pk) if pk else None
    form = ProdutoForm(request.POST or None, request.FILES or None, instance=produto)
    if request.method == "POST" and form.is_valid():
        obj = form.save()
        messages.success(request, f"Produto {obj.sku} salvo.")
        return redirect("produto_detalhe", pk=obj.pk)
    return render(request, "produtos/form.html", {"form": form, "produto": produto})


@administrador
@require_POST
def produto_excluir(request, pk):
    produto = get_object_or_404(Produto, pk=pk)
    if produto.movimentacoes.exists() or produto.itempedido_set.exists():
        produto.ativo = False
        produto.save(update_fields=["ativo"])
        messages.info(request, "O produto tem histórico e não pode ser excluído; foi apenas inativado.")
        return redirect("produto_detalhe", pk=pk)
    produto.delete()
    messages.success(request, "Produto excluído.")
    return redirect("produto_lista")


# ------------------------------------------------------------------ cadastros auxiliares
CADASTROS = {
    "categorias": {
        "model": Categoria, "form": CategoriaForm, "titulo": "Categorias", "singular": "categoria", "genero": "a",
        "colunas": ["Nome", "Descrição", "Produtos"],
        "qs": lambda: Categoria.objects.annotate(n=Count("produtos")),
        "linha": lambda o: [o.nome, o.descricao or "—", o.n],
        "busca": ("nome",), "subtitulo": "Agrupam os produtos e alimentam os relatórios por categoria.",
    },
    "fornecedores": {
        "model": Fornecedor, "form": FornecedorForm, "titulo": "Fornecedores", "singular": "fornecedor", "genero": "o",
        "colunas": ["Fornecedor", "Contato", "E-mail", "Prazo", "Produtos"],
        "qs": lambda: Fornecedor.objects.annotate(n=Count("produtos")),
        "linha": lambda o: [o.nome + ("" if o.ativo else " (inativo)"), o.contato or "—", o.email or "—", f"{o.prazo_entrega_dias} dias", o.n],
        "busca": ("nome", "email", "contato"), "subtitulo": "Quem abastece a operação e os prazos de entrega.",
    },
    "depositos": {
        "model": Deposito, "form": DepositoForm, "titulo": "Depósitos", "singular": "depósito", "genero": "o",
        "colunas": ["Código", "Nome", "Endereço", "Itens em estoque"],
        "qs": lambda: Deposito.objects.annotate(n=Sum("movimentacoes__quantidade")),
        "linha": lambda o: [o.codigo, o.nome + ("" if o.ativo else " (inativo)"), o.endereco or "—", o.n or 0],
        "busca": ("nome", "codigo"), "subtitulo": "Locais físicos onde o estoque é guardado.",
    },
}


def _cfg(slug):
    if slug not in CADASTROS:
        raise Http404
    return CADASTROS[slug]


def cadastro_lista(request, slug):
    cfg = _cfg(slug)
    qs = cfg["qs"]()
    q = request.GET.get("q", "").strip()
    if q:
        f = Q()
        for campo in cfg["busca"]:
            f |= Q(**{f"{campo}__icontains": q})
        qs = qs.filter(f)
    pagina, qs_str = paginar(request, qs, 20)
    linhas = [(o, cfg["linha"](o)) for o in pagina]
    return render(request, "cadastros/lista.html", {"cfg": cfg, "slug": slug, "pagina": pagina, "linhas": linhas, "qs": qs_str})


@operador
def cadastro_form(request, slug, pk=None):
    cfg = _cfg(slug)
    obj = get_object_or_404(cfg["model"], pk=pk) if pk else None
    form = cfg["form"](request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Cadastro salvo.")
        return redirect("cadastro_lista", slug=slug)
    return render(request, "cadastros/form.html", {"cfg": cfg, "slug": slug, "form": form, "obj": obj})


@administrador
@require_POST
def cadastro_excluir(request, slug, pk):
    cfg = _cfg(slug)
    obj = get_object_or_404(cfg["model"], pk=pk)
    try:
        obj.delete()
        messages.success(request, "Cadastro excluído.")
    except Exception:  # ProtectedError: há registros vinculados
        messages.error(request, "Não é possível excluir: existem registros vinculados. Inative o cadastro, se preferir.")
    return redirect("cadastro_lista", slug=slug)


# ------------------------------------------------------------------ movimentações
def mov_lista(request):
    qs = Movimentacao.objects.select_related("produto", "deposito", "usuario")
    q = request.GET.get("q", "").strip()
    tipo = request.GET.get("tipo", "")
    dep = request.GET.get("deposito", "")
    de, ate = _data(request.GET.get("de")), _data(request.GET.get("ate"))
    if q:
        qs = qs.filter(Q(produto__nome__icontains=q) | Q(produto__sku__icontains=q) | Q(documento__icontains=q))
    if tipo in dict(T.choices):
        qs = qs.filter(tipo=tipo)
    if dep.isdigit():
        qs = qs.filter(deposito_id=dep)
    if de:
        qs = qs.filter(data__date__gte=de)
    if ate:
        qs = qs.filter(data__date__lte=ate)
    if request.GET.get("export") == "csv":
        return csv_response(
            "movimentacoes", ["Data", "SKU", "Produto", "Tipo", "Depósito", "Quantidade", "Documento", "Observação", "Usuário"],
            [(timezone.localtime(m.data).strftime("%d/%m/%Y %H:%M"), m.produto.sku, m.produto.nome, m.get_tipo_display(),
              m.deposito.nome, m.quantidade, m.documento, m.observacao, m.usuario.username if m.usuario else "")
             for m in qs[:20000]],
        )
    pagina, qs_str = paginar(request, qs, 25)
    return render(request, "movimentacoes/lista.html", {
        "pagina": pagina, "qs": qs_str, "tipos": T.choices, "depositos": Deposito.objects.all(), "total": pagina.paginator.count,
    })


TIPOS_FORM = {
    "entrada": ("Nova entrada", "Registre o recebimento de mercadoria (compra avulsa)."),
    "saida": ("Nova saída", "Venda, consumo interno ou perda/avaria."),
    "ajuste": ("Ajuste de estoque", "Correção manual com justificativa obrigatória."),
}


@operador
def mov_nova(request, tipo):
    if tipo not in TIPOS_FORM:
        raise Http404
    inicial = {}
    if request.GET.get("produto", "").isdigit():
        inicial["produto"] = request.GET["produto"]
    form = MovimentacaoForm(request.POST or None, tipo=tipo, initial=inicial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            if tipo == "entrada":
                m = services.registrar_entrada(d["produto"], d["deposito"], d["quantidade"], request.user, custo=d.get("custo_unitario"),
                                               documento=d["documento"], obs=d["observacao"])
            elif tipo == "saida":
                m = services.registrar_saida(d["produto"], d["deposito"], d["quantidade"], request.user, motivo=d["motivo"],
                                             preco=d.get("preco_unitario"), documento=d["documento"], obs=d["observacao"])
            else:
                m = services.registrar_ajuste(d["produto"], d["deposito"], d["quantidade"], request.user, obs=d["observacao"])
        except ErroEstoque as e:
            form.add_error(None, str(e))
        else:
            messages.success(request, f"{m.get_tipo_display()} registrada. Novo saldo de {d['produto'].sku}: {services.saldo(d['produto'])}.")
            return redirect("produto_detalhe", pk=d["produto"].pk)
    titulo, sub = TIPOS_FORM[tipo]
    return render(request, "movimentacoes/form.html", {"form": form, "titulo": titulo, "subtitulo": sub, "tipo": tipo})


@operador
def transferencia_nova(request):
    inicial = {"produto": request.GET["produto"]} if request.GET.get("produto", "").isdigit() else {}
    form = TransferenciaForm(request.POST or None, initial=inicial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            services.transferir(d["produto"], d["origem"], d["destino"], d["quantidade"], request.user, obs=d["observacao"])
        except ErroEstoque as e:
            form.add_error(None, str(e))
        else:
            messages.success(request, f"Transferência de {d['quantidade']} × {d['produto'].sku} concluída: {d['origem'].codigo} → {d['destino'].codigo}.")
            return redirect("produto_detalhe", pk=d["produto"].pk)
    return render(request, "movimentacoes/transferir.html", {"form": form})


# ------------------------------------------------------------------ compras
def compra_lista(request):
    qs = PedidoCompra.objects.select_related("fornecedor", "deposito").annotate(
        n_itens=Count("itens"),
        valor=Sum(F("itens__quantidade") * F("itens__custo_unitario"), output_field=DecimalField(max_digits=16, decimal_places=2)),
    )
    status = request.GET.get("status", "")
    q = request.GET.get("q", "").strip()
    if status in dict(PedidoCompra.Status.choices):
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(Q(fornecedor__nome__icontains=q) | Q(observacao__icontains=q) | (Q(pk=int(q.upper().replace("PC-", ""))) if q.upper().replace("PC-", "").isdigit() else Q(pk__in=[])))
    if request.GET.get("export") == "csv":
        return csv_response("pedidos-compra", ["Número", "Fornecedor", "Destino", "Status", "Itens", "Valor", "Criado em"],
                            [(p.numero, p.fornecedor.nome, p.deposito.nome, p.get_status_display(), p.n_itens, p.valor or 0,
                              timezone.localtime(p.criado_em).strftime("%d/%m/%Y")) for p in qs])
    pagina, qs_str = paginar(request, qs, 15)
    contagem = dict(PedidoCompra.objects.values_list("status").annotate(n=Count("id")))
    return render(request, "compras/lista.html", {
        "pagina": pagina, "qs": qs_str, "status_opcoes": PedidoCompra.Status.choices, "contagem": contagem,
    })


@operador
def compra_form(request, pk=None):
    pedido = get_object_or_404(PedidoCompra, pk=pk) if pk else None
    if pedido and not pedido.editavel:
        messages.error(request, "Só é possível editar pedidos em rascunho.")
        return redirect("compra_detalhe", pk=pk)
    FormSet = item_formset(extra=1 if pedido else 3)
    inicial_itens = []
    if not pedido and request.GET.get("produto", "").isdigit():
        p = Produto.objects.filter(pk=request.GET["produto"]).first()
        if p:
            inicial_itens = [{"produto": p, "quantidade": max(2 * p.estoque_minimo, 1), "custo_unitario": p.custo}]
            FormSet = item_formset(extra=3)
    form = PedidoForm(request.POST or None, instance=pedido,
                      initial={"fornecedor": p.fornecedor_id} if inicial_itens and p.fornecedor_id else None)
    formset = FormSet(request.POST or None, instance=pedido, initial=inicial_itens if not pedido else None)
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        ativos = [f for f in formset.forms if f.cleaned_data and not f.cleaned_data.get("DELETE")]
        if not ativos:
            form.add_error(None, "Adicione ao menos um item ao pedido.")
        else:
            with transaction.atomic():
                obj = form.save(commit=False)
                if not obj.pk:
                    obj.usuario = request.user
                obj.save()
                formset.instance = obj
                formset.save()
            messages.success(request, f"Pedido {obj.numero} salvo como rascunho.")
            return redirect("compra_detalhe", pk=obj.pk)
    return render(request, "compras/form.html", {"form": form, "formset": formset, "pedido": pedido})


def compra_detalhe(request, pk):
    pedido = get_object_or_404(PedidoCompra.objects.select_related("fornecedor", "deposito", "usuario"), pk=pk)
    itens = pedido.itens.select_related("produto")
    return render(request, "compras/detalhe.html", {"pedido": pedido, "itens": itens, "total": sum((i.subtotal for i in itens), Decimal("0"))})


@operador
@require_POST
def compra_acao(request, pk, acao):
    pedido = get_object_or_404(PedidoCompra, pk=pk)
    try:
        if acao == "enviar":
            services.enviar_pedido(pedido, request.user)
            messages.success(request, f"Pedido {pedido.numero} enviado ao fornecedor (simulado: nenhum e-mail é disparado).")
        elif acao == "receber":
            services.receber_pedido(pedido, request.user)
            messages.success(request, f"Pedido {pedido.numero} recebido: entradas lançadas no estoque.")
        elif acao == "cancelar":
            services.cancelar_pedido(pedido)
            messages.info(request, f"Pedido {pedido.numero} cancelado.")
        else:
            raise Http404
    except ErroEstoque as e:
        messages.error(request, str(e))
    return redirect("compra_detalhe", pk=pk)


# ------------------------------------------------------------------ alertas, ABC
ALERTAS = {
    "minimo": ("Abaixo do mínimo", "Saldo positivo, porém igual ou inferior ao estoque mínimo."),
    "ruptura": ("Ruptura", "Produtos zerados: venda perdida até a reposição."),
    "sem_giro": ("Sem giro", f"Com saldo, mas sem nenhuma saída nos últimos {settings.DIAS_SEM_GIRO} dias: capital parado."),
}


def alertas(request):
    tipo = request.GET.get("tipo", "minimo")
    if tipo not in ALERTAS:
        tipo = "minimo"
    itens = list(services.produtos_em_alerta(tipo))
    agora = timezone.now()
    for p in itens:
        p.sugerido = services.sugestao_compra(p) if tipo != "sem_giro" else None
        p.dias_sem_saida = (agora - p.ultima_saida).days if p.ultima_saida else None
    if request.GET.get("export") == "csv":
        return csv_response(f"alerta-{tipo}", ["SKU", "Produto", "Categoria", "Saldo", "Mínimo", "Sugestão de compra", "Valor em estoque", "Fornecedor"],
                            [(p.sku, p.nome, p.categoria.nome, p.saldo, p.estoque_minimo, p.sugerido or "", p.valor_estoque,
                              p.fornecedor.nome if p.fornecedor else "") for p in itens])
    pagina, qs_str = paginar(request, itens, 20)
    return render(request, "alertas.html", {
        "tipo": tipo, "tipos": ALERTAS, "contagem": services.contagem_alertas(), "pagina": pagina, "qs": qs_str,
        "titulo_tipo": ALERTAS[tipo][0], "descricao_tipo": ALERTAS[tipo][1],
        "valor_total": sum((p.valor_estoque for p in itens), Decimal("0")),
    })


@operador
@require_POST
def alertas_gerar_compras(request):
    try:
        criados = services.gerar_sugestoes_compra(request.user)
    except ErroEstoque as e:
        messages.error(request, str(e))
        return redirect("alertas")
    if criados:
        messages.success(request, f"{len(criados)} rascunho(s) de compra criado(s), um por fornecedor. Revise e envie.")
        return redirect("compra_lista")
    messages.info(request, "Nenhum item novo para repor: os alertas já têm pedidos em aberto.")
    return redirect("alertas")


def curva_abc(request):
    linhas, resumo, total = services.curva_abc(90)
    if request.GET.get("export") == "csv":
        return csv_response("curva-abc", ["Classe", "SKU", "Produto", "Qtd consumida (90d)", "Valor consumido", "% do total", "% acumulado", "Saldo"],
                            [(l["classe"], l["produto"].sku, l["produto"].nome, l["qtd"], l["valor"], f"{l['pct']:.1f}".replace(".", ","),
                              f"{l['acum']:.1f}".replace(".", ","), l["produto"].saldo) for l in linhas])
    classe = request.GET.get("classe", "")
    if classe in ("A", "B", "C"):
        linhas = [l for l in linhas if l["classe"] == classe]
    pagina, qs_str = paginar(request, linhas, 20)
    n = sum(r["qtd"] for r in resumo.values()) or 1
    for r in resumo.values():
        r["pct_itens"] = r["qtd"] / n * 100
    return render(request, "abc.html", {"pagina": pagina, "qs": qs_str, "resumo": resumo, "total": total, "classe": classe})


# ------------------------------------------------------------------ inventário
def inventario_lista(request):
    qs = Inventario.objects.select_related("deposito", "usuario").annotate(
        n=Count("itens"), contados=Count("itens", filter=Q(itens__qtd_contada__isnull=False)),
        divergentes=Count("itens", filter=~Q(itens__diferenca=0) & Q(itens__diferenca__isnull=False)),
    )
    pagina, qs_str = paginar(request, qs, 15)
    return render(request, "inventarios/lista.html", {"pagina": pagina, "qs": qs_str})


@operador
def inventario_novo(request):
    form = NovoInventarioForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            inv = services.abrir_inventario(form.cleaned_data["deposito"], request.user, form.cleaned_data["observacao"])
        except ErroEstoque as e:
            form.add_error(None, str(e))
        else:
            messages.success(request, f"Inventário {inv.numero} aberto. Lance as contagens.")
            return redirect("inventario_detalhe", pk=inv.pk)
    return render(request, "inventarios/novo.html", {"form": form})


def inventario_detalhe(request, pk):
    inv = get_object_or_404(Inventario.objects.select_related("deposito"), pk=pk)
    itens = inv.itens.select_related("produto__categoria")
    filtro = request.GET.get("ver", "")
    q = request.GET.get("q", "").strip()
    if request.method == "POST":
        if request.nivel < 2:
            raise PermissionDenied
        if inv.status != Inventario.Status.ABERTO:
            messages.error(request, "Inventário já finalizado.")
            return redirect("inventario_detalhe", pk=pk)
        erros = 0
        with transaction.atomic():
            for item in itens:
                bruto = request.POST.get(f"c_{item.pk}")
                if bruto is None:
                    continue
                bruto = bruto.strip()
                if bruto == "":
                    novo = None
                elif bruto.isdigit():
                    novo = int(bruto)
                else:
                    erros += 1
                    continue
                if novo != item.qtd_contada:
                    item.qtd_contada = novo
                    item.save(update_fields=["qtd_contada"])
        if erros:
            messages.error(request, f"{erros} valor(es) inválido(s) ignorado(s): use números inteiros não negativos.")
        if request.POST.get("acao") == "finalizar":
            try:
                n = services.finalizar_inventario(inv, request.user)
            except ErroEstoque as e:
                messages.error(request, str(e))
            else:
                messages.success(request, f"Inventário finalizado: {n} ajuste(s) lançado(s) no estoque.")
        else:
            messages.success(request, "Contagens salvas.")
        return redirect(request.get_full_path())
    todos = list(itens)
    contados = sum(1 for i in todos if i.qtd_contada is not None)
    diverg = sum(1 for i in todos if i.qtd_contada is not None and i.qtd_contada != i.qtd_sistema)
    exibir = todos
    if filtro == "pendentes":
        exibir = [i for i in todos if i.qtd_contada is None]
    elif filtro == "divergentes":
        exibir = [i for i in todos if i.qtd_contada is not None and i.qtd_contada != i.qtd_sistema]
    if q:
        exibir = [i for i in exibir if q.lower() in i.produto.nome.lower() or q.lower() in i.produto.sku.lower()]
    if request.GET.get("export") == "csv":
        return csv_response(f"inventario-{inv.numero}", ["SKU", "Produto", "Saldo sistema", "Contagem", "Diferença"],
                            [(i.produto.sku, i.produto.nome, i.qtd_sistema, "" if i.qtd_contada is None else i.qtd_contada,
                              i.diferenca if i.diferenca is not None else ("" if i.qtd_contada is None else i.qtd_contada - i.qtd_sistema)) for i in exibir])
    pagina, qs_str = paginar(request, exibir, 40)
    for i in pagina:
        i.dif = i.diferenca if i.diferenca is not None else (None if i.qtd_contada is None else i.qtd_contada - i.qtd_sistema)
    return render(request, "inventarios/detalhe.html", {
        "inv": inv, "pagina": pagina, "qs": qs_str, "total": len(todos), "contados": contados, "diverg": diverg,
        "filtro": filtro, "aberto": inv.status == Inventario.Status.ABERTO,
        "progresso": int(contados / len(todos) * 100) if todos else 0,
    })


# ------------------------------------------------------------------ usuários e API
@administrador
def usuarios(request):
    if request.method == "POST":
        alvo = get_object_or_404(User, pk=request.POST.get("user_id"))
        papel = request.POST.get("papel")
        if alvo.is_superuser or alvo == request.user:
            messages.error(request, "Não é possível alterar o papel deste usuário.")
        elif papel in dict(Papel.choices):
            Perfil.objects.update_or_create(user=alvo, defaults={"papel": papel})
            messages.success(request, f"Papel de {alvo.username} atualizado.")
        return redirect("usuarios")
    lista = []
    for u in User.objects.order_by("username"):
        lista.append((u, papel_de(u)))
    return render(request, "usuarios.html", {"lista": lista, "papeis": Papel.choices})


def papel_de(u):
    from .models import papel_do_usuario
    return papel_do_usuario(u)


@login_required
def api_docs(request):
    usuario_demo = request.user.username.startswith("demo")
    token = ApiToken.objects.filter(user=request.user).first()
    if request.method == "POST":
        if usuario_demo:
            messages.info(request, "Na demonstração o token é compartilhado e não pode ser regenerado.")
        else:
            token = ApiToken.gerar(request.user)
            messages.success(request, "Novo token gerado. O anterior deixou de funcionar.")
        return redirect("api_docs")
    if token is None:
        token = ApiToken.gerar(request.user)
    return render(request, "api.html", {"token": token, "host": request.build_absolute_uri("/").rstrip("/"), "usuario_demo": usuario_demo})


def api_token(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        cab = request.headers.get("Authorization", "")
        chave = cab[7:].strip() if cab.lower().startswith("bearer ") else request.headers.get("X-API-Token", "").strip()
        tok = ApiToken.objects.select_related("user").filter(chave=chave, user__is_active=True).first() if chave else None
        if not tok:
            resp = JsonResponse({"erro": "Token ausente ou inválido. Envie 'Authorization: Bearer <token>'."}, status=401)
            resp["WWW-Authenticate"] = "Bearer"
            return resp
        request.user = tok.user
        return view(request, *args, **kwargs)
    return wrapper


def _paginacao_api(request, qs):
    try:
        tam = min(max(int(request.GET.get("page_size", 50)), 1), 200)
    except ValueError:
        tam = 50
    pag = Paginator(qs, tam).get_page(request.GET.get("page"))
    return pag, {"count": pag.paginator.count, "page": pag.number, "pages": pag.paginator.num_pages, "page_size": tam}


@api_token
def api_produtos(request):
    qs = Produto.objects.com_saldo().select_related("categoria").order_by("sku")
    if request.GET.get("ativo", "1") != "todos":
        qs = qs.filter(ativo=True)
    if q := request.GET.get("q", "").strip():
        qs = qs.filter(Q(nome__icontains=q) | Q(sku__icontains=q))
    if cat := request.GET.get("categoria", "").strip():
        qs = qs.filter(categoria__nome__iexact=cat)
    if sit := request.GET.get("situacao", ""):
        qs = qs.filtrar_situacao(sit)
    pag, meta = _paginacao_api(request, qs)
    meta["results"] = [{
        "sku": p.sku, "nome": p.nome, "categoria": p.categoria.nome, "unidade": p.unidade,
        "custo": str(p.custo), "preco": str(p.preco), "estoque_minimo": p.estoque_minimo, "saldo": p.saldo,
        "valor_em_estoque": str(p.valor_estoque), "situacao": p.situacao, "ativo": p.ativo,
    } for p in pag]
    return JsonResponse(meta, json_dumps_params={"ensure_ascii": False})


@api_token
def api_saldo(request):
    qs = Movimentacao.objects.values("produto__sku", "produto__nome", "deposito__codigo", "deposito__nome")
    if sku := request.GET.get("sku", "").strip():
        if not Produto.objects.filter(sku__iexact=sku).exists():
            return JsonResponse({"erro": "SKU não encontrado."}, status=404)
        qs = qs.filter(produto__sku__iexact=sku)
    if dep := request.GET.get("deposito", "").strip():
        qs = qs.filter(deposito__codigo__iexact=dep)
    qs = qs.annotate(saldo=Sum("quantidade")).exclude(saldo=0).order_by("produto__sku", "deposito__codigo")
    pag, meta = _paginacao_api(request, qs)
    meta["results"] = [{
        "sku": r["produto__sku"], "produto": r["produto__nome"], "deposito": r["deposito__codigo"],
        "deposito_nome": r["deposito__nome"], "saldo": r["saldo"],
    } for r in pag]
    return JsonResponse(meta, json_dumps_params={"ensure_ascii": False})
