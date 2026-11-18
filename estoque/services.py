"""Regras de negócio do estoque. Toda alteração de saldo passa por aqui."""
import math
import uuid
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import (
    Deposito, Inventario, ItemInventario, ItemPedido, Movimentacao, PedidoCompra, Produto,
)

T = Movimentacao.Tipo


class ErroEstoque(Exception):
    """Violação de regra de negócio (mensagem já em português, pronta para exibir)."""


def saldo(produto, deposito=None):
    qs = Movimentacao.objects.filter(produto=produto)
    if deposito is not None:
        qs = qs.filter(deposito=deposito)
    return qs.aggregate(s=Sum("quantidade"))["s"] or 0


def _travar(produto):
    # select_for_update serializa operações concorrentes no mesmo produto (Postgres).
    return Produto.objects.select_for_update().get(pk=produto.pk)


def _criar(produto, deposito, tipo, qtd, usuario, **extra):
    if qtd == 0:
        raise ErroEstoque("A quantidade não pode ser zero.")
    if not deposito.ativo:
        raise ErroEstoque("O depósito está inativo.")
    return Movimentacao.objects.create(
        produto=produto, deposito=deposito, tipo=tipo, quantidade=qtd, usuario=usuario, **extra
    )


@transaction.atomic
def registrar_entrada(produto, deposito, qtd, usuario, custo=None, documento="", obs="", data=None, pedido=None):
    if qtd <= 0:
        raise ErroEstoque("A quantidade da entrada deve ser maior que zero.")
    p = _travar(produto)
    return _criar(
        p, deposito, T.ENTRADA, qtd, usuario,
        custo_unitario=custo if custo is not None else p.custo,
        documento=documento, observacao=obs, data=data or timezone.now(), pedido=pedido,
    )


@transaction.atomic
def registrar_saida(produto, deposito, qtd, usuario, motivo="venda", preco=None, documento="", obs="", data=None):
    if qtd <= 0:
        raise ErroEstoque("A quantidade da saída deve ser maior que zero.")
    p = _travar(produto)
    disponivel = saldo(p, deposito)
    if qtd > disponivel:
        raise ErroEstoque(
            f"Saldo insuficiente em {deposito.nome}: disponível {disponivel}, solicitado {qtd}."
        )
    if preco is None and motivo == Movimentacao.Motivo.VENDA:
        preco = p.preco
    return _criar(
        p, deposito, T.SAIDA, -qtd, usuario, motivo=motivo, custo_unitario=p.custo,
        preco_unitario=preco if motivo == Movimentacao.Motivo.VENDA else None,
        documento=documento, observacao=obs, data=data or timezone.now(),
    )


@transaction.atomic
def registrar_ajuste(produto, deposito, delta, usuario, obs="", data=None, inventario=None):
    p = _travar(produto)
    if delta < 0 and -delta > saldo(p, deposito):
        raise ErroEstoque(f"O ajuste deixaria o saldo negativo em {deposito.nome}.")
    return _criar(
        p, deposito, T.AJUSTE, delta, usuario, custo_unitario=p.custo, observacao=obs,
        data=data or timezone.now(), inventario=inventario,
    )


@transaction.atomic
def transferir(produto, origem, destino, qtd, usuario, obs="", data=None):
    if origem.pk == destino.pk:
        raise ErroEstoque("Origem e destino devem ser depósitos diferentes.")
    if qtd <= 0:
        raise ErroEstoque("A quantidade da transferência deve ser maior que zero.")
    p = _travar(produto)
    disponivel = saldo(p, origem)
    if qtd > disponivel:
        raise ErroEstoque(f"Saldo insuficiente em {origem.nome}: disponível {disponivel}, solicitado {qtd}.")
    tid = uuid.uuid4()
    data = data or timezone.now()
    comum = dict(custo_unitario=p.custo, data=data, transferencia=tid)
    saida = _criar(p, origem, T.TRANSF_SAIDA, -qtd, usuario, observacao=obs or f"Para {destino.nome}", **comum)
    entrada = _criar(p, destino, T.TRANSF_ENTRADA, qtd, usuario, observacao=obs or f"De {origem.nome}", **comum)
    return saida, entrada


# ---------------------------------------------------------------- compras
@transaction.atomic
def enviar_pedido(pedido, usuario=None):
    pedido = PedidoCompra.objects.select_for_update().get(pk=pedido.pk)
    if pedido.status != PedidoCompra.Status.RASCUNHO:
        raise ErroEstoque("Somente rascunhos podem ser enviados.")
    if not pedido.itens.exists():
        raise ErroEstoque("Adicione ao menos um item antes de enviar.")
    pedido.status = PedidoCompra.Status.ENVIADO
    pedido.enviado_em = timezone.now()
    if not pedido.previsao_entrega:
        pedido.previsao_entrega = (timezone.now() + timedelta(days=pedido.fornecedor.prazo_entrega_dias)).date()
    pedido.save()
    return pedido


@transaction.atomic
def receber_pedido(pedido, usuario, data=None):
    """Recebe o pedido: gera uma ENTRADA por item e atualiza o último custo de compra."""
    pedido = PedidoCompra.objects.select_for_update().get(pk=pedido.pk)
    if pedido.status != PedidoCompra.Status.ENVIADO:
        raise ErroEstoque("Somente pedidos enviados podem ser recebidos.")
    data = data or timezone.now()
    for item in pedido.itens.select_related("produto"):
        registrar_entrada(
            item.produto, pedido.deposito, item.quantidade, usuario, custo=item.custo_unitario,
            documento=pedido.numero, obs=f"Recebimento {pedido.numero}", data=data, pedido=pedido,
        )
        if item.produto.custo != item.custo_unitario:
            Produto.objects.filter(pk=item.produto_id).update(custo=item.custo_unitario)
    pedido.status = PedidoCompra.Status.RECEBIDO
    pedido.recebido_em = data
    pedido.save()
    return pedido


@transaction.atomic
def cancelar_pedido(pedido):
    pedido = PedidoCompra.objects.select_for_update().get(pk=pedido.pk)
    if pedido.status not in (PedidoCompra.Status.RASCUNHO, PedidoCompra.Status.ENVIADO):
        raise ErroEstoque("Este pedido não pode mais ser cancelado.")
    pedido.status = PedidoCompra.Status.CANCELADO
    pedido.save()
    return pedido


# ---------------------------------------------------------------- inventário
@transaction.atomic
def abrir_inventario(deposito, usuario, obs=""):
    if Inventario.objects.filter(deposito=deposito, status=Inventario.Status.ABERTO).exists():
        raise ErroEstoque("Já existe um inventário em contagem para este depósito.")
    inv = Inventario.objects.create(deposito=deposito, usuario=usuario, observacao=obs)
    produtos = Produto.objects.filter(ativo=True).com_saldo(deposito).order_by("nome")
    ItemInventario.objects.bulk_create(
        ItemInventario(inventario=inv, produto=p, qtd_sistema=p.saldo_dep) for p in produtos
    )
    return inv


@transaction.atomic
def finalizar_inventario(inv, usuario, data=None):
    """Compara a contagem com o saldo ATUAL do depósito e lança um ajuste por divergência."""
    inv = Inventario.objects.select_for_update().get(pk=inv.pk)
    if inv.status != Inventario.Status.ABERTO:
        raise ErroEstoque("Este inventário já foi finalizado.")
    ajustes = 0
    for item in inv.itens.select_related("produto").exclude(qtd_contada__isnull=True):
        atual = saldo(item.produto, inv.deposito)
        diff = item.qtd_contada - atual
        item.qtd_sistema = atual
        item.diferenca = diff
        item.save(update_fields=["qtd_sistema", "diferenca"])
        if diff:
            registrar_ajuste(
                item.produto, inv.deposito, diff, usuario,
                obs=f"Inventário {inv.numero}: contado {item.qtd_contada}, sistema {atual}",
                data=data, inventario=inv,
            )
            ajustes += 1
    inv.status = Inventario.Status.FINALIZADO
    inv.finalizado_em = data or timezone.now()
    inv.save()
    return ajustes


# ---------------------------------------------------------------- análises
def sugestao_compra(produto):
    """Quantidade sugerida para voltar a ~2x o estoque mínimo."""
    return max(2 * produto.estoque_minimo - produto.saldo, 1)


def produtos_em_alerta(tipo):
    qs = Produto.objects.filter(ativo=True).com_saldo().select_related("categoria", "fornecedor")
    return qs.filtrar_situacao(tipo).order_by("nome")


def contagem_alertas():
    base = Produto.objects.filter(ativo=True).com_saldo()
    return {
        "minimo": base.filtrar_situacao("minimo").count(),
        "ruptura": base.filtrar_situacao("ruptura").count(),
        "sem_giro": base.filtrar_situacao("sem_giro").count(),
    }


@transaction.atomic
def gerar_sugestoes_compra(usuario):
    """Cria rascunhos de compra (um por fornecedor) para itens abaixo do mínimo ou em ruptura."""
    deposito = Deposito.objects.filter(ativo=True).order_by("codigo").first()
    if not deposito:
        raise ErroEstoque("Cadastre um depósito antes.")
    grupos = defaultdict(list)
    for tipo in ("ruptura", "minimo"):
        for p in produtos_em_alerta(tipo):
            if p.fornecedor_id:
                grupos[p.fornecedor_id].append(p)
    criados = []
    for fid, produtos in grupos.items():
        ja_pedido = set(
            ItemPedido.objects.filter(
                pedido__fornecedor_id=fid, pedido__status__in=["rascunho", "enviado"]
            ).values_list("produto_id", flat=True)
        )
        produtos = [p for p in produtos if p.pk not in ja_pedido]
        if not produtos:
            continue
        pedido = PedidoCompra.objects.create(
            fornecedor_id=fid, deposito=deposito, usuario=usuario, observacao="Sugestão automática de reposição"
        )
        ItemPedido.objects.bulk_create(
            ItemPedido(pedido=pedido, produto=p, quantidade=sugestao_compra(p), custo_unitario=p.custo)
            for p in produtos
        )
        criados.append(pedido)
    return criados


def curva_abc(dias=90):
    """Classifica produtos pelo valor consumido (saídas a custo) no período: A até 80%, B até 95%, C o resto."""
    desde = timezone.now() - timedelta(days=dias)
    consumo = {
        r["produto_id"]: -r["q"]
        for r in Movimentacao.objects.filter(tipo=T.SAIDA, data__gte=desde)
        .values("produto_id").annotate(q=Sum("quantidade"))
    }
    produtos = list(Produto.objects.filter(ativo=True).com_saldo().select_related("categoria"))
    linhas = []
    for p in produtos:
        qtd = consumo.get(p.pk, 0)
        linhas.append({"produto": p, "qtd": qtd, "valor": qtd * p.custo})
    linhas.sort(key=lambda l: (-l["valor"], l["produto"].nome))
    total = sum((l["valor"] for l in linhas), Decimal("0"))
    acum = Decimal("0")
    for l in linhas:
        pct = (l["valor"] / total * 100) if total else Decimal("0")
        anterior = acum
        acum += pct
        l["pct"], l["acum"] = pct, acum
        if l["valor"] <= 0:
            l["classe"] = "C"
        elif anterior < 80:
            l["classe"] = "A"
        elif anterior < 95:
            l["classe"] = "B"
        else:
            l["classe"] = "C"
    resumo = {}
    for c in "ABC":
        itens = [l for l in linhas if l["classe"] == c]
        resumo[c] = {
            "qtd": len(itens), "valor": sum((l["valor"] for l in itens), Decimal("0")),
            "pct": sum((l["pct"] for l in itens), Decimal("0")),
        }
    return linhas, resumo, total


def kardex(produto, deposito=None, tipo=None):
    """Movimentações em ordem cronológica com saldo corrente. Retorna lista (mais recente primeiro)."""
    qs = Movimentacao.objects.filter(produto=produto).select_related("deposito", "usuario").order_by("data", "id")
    if deposito:
        qs = qs.filter(deposito=deposito)
    corrente = 0
    linhas = []
    for m in qs:
        corrente += m.quantidade
        m.saldo_corrente = corrente
        linhas.append(m)
    if tipo:
        linhas = [m for m in linhas if m.tipo == tipo]
    linhas.reverse()
    return linhas


def cobertura_dias(produto, dias=30):
    """Dias de cobertura do saldo atual pelo consumo médio recente (None se sem consumo)."""
    desde = timezone.now() - timedelta(days=dias)
    q = -(Movimentacao.objects.filter(produto=produto, tipo=T.SAIDA, data__gte=desde)
          .aggregate(s=Sum("quantidade"))["s"] or 0)
    if q <= 0:
        return None
    return math.floor(saldo(produto) / (q / dias))
