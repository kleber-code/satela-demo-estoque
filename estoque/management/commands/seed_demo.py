"""Popula o sistema com dados fictícios de uma distribuidora (Distribuidora Aurora).

Idempotente: se já houver produtos, não faz nada (use --reset para recriar).
Determinístico: usa semente fixa; as datas são relativas a "hoje".
"""
import math
import random
import uuid
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from estoque.models import (
    Categoria, Deposito, Fornecedor, Inventario, ItemInventario, ItemPedido, Movimentacao, Papel,
    PedidoCompra, Perfil, Produto,
)

DIAS = 180

FORNECEDORES = [
    ("Grãos do Sul Atacado Ltda", "11.000.000/0001-01", "Equipe Comercial", "vendas@graosdosul.exemplo.com.br", "(11) 5555-0101", 5),
    ("Bebidas Rio Verde S.A.", "11.000.000/0001-02", "Equipe Comercial", "pedidos@rioverde.exemplo.com.br", "(11) 5555-0102", 4),
    ("Limpa Max Química", "11.000.000/0001-03", "Atendimento", "contato@limpamax.exemplo.com.br", "(11) 5555-0103", 7),
    ("Higiene Plena Indústria", "11.000.000/0001-04", "Atendimento", "vendas@higieneplena.exemplo.com.br", "(11) 5555-0104", 6),
    ("EmbalaPack Descartáveis", "11.000.000/0001-05", "Equipe Comercial", "comercial@embalapack.exemplo.com.br", "(11) 5555-0105", 8),
    ("Papelaria Central Atacado", "11.000.000/0001-06", "Atendimento", "atacado@papelariacentral.exemplo.com.br", "(11) 5555-0106", 6),
    ("Frigorífico Vale Azul", "11.000.000/0001-07", "Equipe Comercial", "pedidos@valeazul.exemplo.com.br", "(11) 5555-0107", 3),
    ("FerroLar Utilidades", "11.000.000/0001-08", "Atendimento", "vendas@ferrolar.exemplo.com.br", "(11) 5555-0108", 9),
]

DEPOSITOS = [
    ("CD01", "Centro de Distribuição", "Rodovia Exemplo, km 12 — Galpão A"),
    ("FN02", "Filial Norte", "Av. das Acácias, 1200 — Zona Norte"),
    ("LJ03", "Loja de Expedição", "Rua do Comércio, 85 — Centro"),
]
PESO_DEP = [0.62, 0.23, 0.15]

# categoria: (prefixo, fornecedor_idx, margem, [(nome, unidade, custo, giro_dia)])
CATALOGO = {
    "Mercearia": ("MER", 0, 0.28, [
        ("Arroz Tipo 1 5kg", "PCT", 21.40, 9), ("Feijão Carioca 1kg", "PCT", 6.90, 11), ("Açúcar Refinado 1kg", "PCT", 3.60, 14),
        ("Café Torrado e Moído 500g", "PCT", 14.80, 8), ("Óleo de Soja 900ml", "UN", 6.45, 12), ("Macarrão Espaguete 500g", "PCT", 3.15, 13),
        ("Farinha de Trigo 1kg", "PCT", 4.20, 7), ("Sal Refinado 1kg", "PCT", 1.85, 6), ("Molho de Tomate 340g", "UN", 2.35, 10),
        ("Biscoito Cream Cracker 400g", "PCT", 4.70, 6)]),
    "Bebidas": ("BEB", 1, 0.30, [
        ("Água Mineral 1,5L (fardo c/ 6)", "FD", 9.20, 10), ("Refrigerante Cola 2L", "UN", 6.80, 13), ("Suco de Uva Integral 1L", "UN", 8.90, 4),
        ("Cerveja Pilsen Lata 350ml (cx c/ 12)", "CX", 38.50, 8), ("Energético 250ml", "UN", 5.60, 5), ("Chá Gelado Limão 1,5L", "UN", 5.10, 4),
        ("Água de Coco 1L", "UN", 9.40, 3), ("Isotônico Laranja 500ml", "UN", 4.30, 5), ("Refrigerante Guaraná 2L", "UN", 6.20, 9),
        ("Leite Integral 1L (cx c/ 12)", "CX", 52.00, 7)]),
    "Limpeza": ("LIM", 2, 0.35, [
        ("Detergente Líquido 500ml", "UN", 1.95, 15), ("Desinfetante Lavanda 2L", "UN", 5.90, 8), ("Água Sanitária 2L", "UN", 4.10, 8),
        ("Sabão em Pó 1,6kg", "UN", 15.70, 6), ("Amaciante 2L", "UN", 9.80, 5), ("Esponja Multiuso (pct c/ 3)", "PCT", 3.40, 7),
        ("Limpa Vidros 500ml", "UN", 5.20, 3), ("Saco de Lixo 100L (pct c/ 10)", "PCT", 11.90, 5), ("Multiuso Cremoso 500ml", "UN", 3.85, 4),
        ("Álcool 70% 1L", "UN", 6.30, 6)]),
    "Higiene Pessoal": ("HIG", 3, 0.38, [
        ("Papel Higiênico Folha Dupla (12 rolos)", "PCT", 16.90, 12), ("Sabonete 90g (cx c/ 12)", "CX", 14.40, 5), ("Creme Dental 90g", "UN", 2.90, 10),
        ("Shampoo 350ml", "UN", 8.70, 5), ("Condicionador 350ml", "UN", 8.90, 4), ("Desodorante Aerossol 150ml", "UN", 9.30, 5),
        ("Absorvente com Abas (pct c/ 8)", "PCT", 3.80, 6), ("Fralda Descartável M (pct c/ 40)", "PCT", 42.00, 4),
        ("Algodão em Disco 50un", "UN", 3.10, 2), ("Escova Dental Macia", "UN", 3.50, 6)]),
    "Descartáveis": ("DES", 4, 0.40, [
        ("Copo Descartável 200ml (pct c/ 100)", "PCT", 5.90, 9), ("Prato Descartável 21cm (pct c/ 10)", "PCT", 2.10, 8),
        ("Guardanapo de Papel (pct c/ 50)", "PCT", 2.40, 8), ("Kit Talher Plástico (pct c/ 100)", "PCT", 13.50, 4),
        ("Marmitex Alumínio 750ml (cx c/ 100)", "CX", 48.00, 3), ("Filme PVC 28cm x 30m", "RL", 7.20, 3), ("Papel Alumínio 30cm x 7,5m", "RL", 6.60, 4),
        ("Sacola Plástica 40x50 (kg)", "KG", 14.90, 3), ("Papel Toalha (pct c/ 2 rolos)", "PCT", 4.40, 7), ("Pote Plástico 500ml (pct c/ 50)", "PCT", 21.00, 2)]),
    "Papelaria e Escritório": ("ESC", 5, 0.42, [
        ("Papel A4 75g (resma 500 folhas)", "RM", 22.90, 6), ("Caneta Esferográfica Azul (cx c/ 50)", "CX", 31.00, 2),
        ("Grampeador de Mesa 26/6", "UN", 17.80, 1), ("Pasta Suspensa (pct c/ 10)", "PCT", 19.90, 1), ("Fita Adesiva 45mm x 100m", "RL", 3.90, 5),
        ("Cartucho de Toner Compatível", "UN", 118.00, 1), ("Bloco Adesivo 76x76mm", "UN", 5.40, 3), ("Marcador de Texto (cx c/ 12)", "CX", 22.50, 1),
        ("Envelope Ofício (cx c/ 100)", "CX", 24.00, 1), ("Clips 2/0 (cx c/ 100)", "CX", 3.20, 3)]),
    "Frios e Congelados": ("FRI", 6, 0.26, [
        ("Hambúrguer Bovino 672g", "PCT", 19.80, 6), ("Pão de Queijo 1kg", "PCT", 17.50, 7), ("Lasanha Bolonhesa 600g", "UN", 15.40, 4),
        ("Muçarela Fatiada (kg)", "KG", 36.90, 8), ("Presunto Cozido Fatiado (kg)", "KG", 27.50, 6), ("Margarina 500g", "UN", 5.90, 9),
        ("Iogurte Natural 170g (cx c/ 8)", "CX", 12.80, 4), ("Queijo Minas Frescal (kg)", "KG", 31.00, 3), ("Sorvete Napolitano 2L", "UN", 16.20, 3),
        ("Pizza Muçarela 460g", "UN", 14.30, 4)]),
    "Utilidades e Manutenção": ("MAN", 7, 0.45, [
        ("Luva Nitrílica (cx c/ 100)", "CX", 39.00, 3), ("Lâmpada LED 9W", "UN", 4.90, 8), ("Fita Isolante 19mm x 10m", "RL", 3.60, 3),
        ("Pilha Alcalina AA (c/ 4)", "PCT", 9.40, 5), ("Extensão Elétrica 3m", "UN", 17.90, 2), ("Cadeado 35mm", "UN", 13.20, 2),
        ("Vassoura de Piaçava", "UN", 11.50, 2), ("Rodo 60cm com Cabo", "UN", 16.80, 2), ("Balde Plástico 12L", "UN", 9.70, 2), ("Fita Crepe 18mm x 50m", "RL", 3.10, 4)]),
}

# Estados finais forçados (para a demonstração mostrar todos os alertas)
RUPTURA = ["Água de Coco 1L", "Cartucho de Toner Compatível", "Hambúrguer Bovino 672g", "Fralda Descartável M (pct c/ 40)", "Lâmpada LED 9W"]
ABAIXO_MIN = ["Café Torrado e Moído 500g", "Refrigerante Cola 2L", "Detergente Líquido 500ml", "Papel Higiênico Folha Dupla (12 rolos)",
              "Papel A4 75g (resma 500 folhas)", "Cerveja Pilsen Lata 350ml (cx c/ 12)", "Sabão em Pó 1,6kg", "Pilha Alcalina AA (c/ 4)"]
SEM_GIRO = ["Suco de Uva Integral 1L", "Limpa Vidros 500ml", "Pasta Suspensa (pct c/ 10)", "Envelope Ofício (cx c/ 100)",
            "Pote Plástico 500ml (pct c/ 50)", "Sorvete Napolitano 2L", "Cadeado 35mm"]


def preco_de(custo, margem):
    bruto = custo * (1 + margem)
    return Decimal(str(math.floor(bruto * 10) / 10 + 0.09)).quantize(Decimal("0.01")) if bruto > 5 else Decimal(str(round(bruto, 2)))


class Command(BaseCommand):
    help = "Cria dados fictícios de demonstração (idempotente)."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Apaga os dados de estoque e recria.")
        parser.add_argument("--se-vazio", action="store_true", help="Só popula se não houver produtos (para release).")

    def handle(self, *args, **opts):
        self._usuarios()
        if opts["reset"]:
            self._limpar()
        elif Produto.objects.exists():
            self.stdout.write("Dados já existem; nada a fazer (use --reset para recriar).")
            return
        with transaction.atomic():
            self._popular()
        self.stdout.write(self.style.SUCCESS(
            f"Demo pronta: {Produto.objects.count()} produtos, {Movimentacao.objects.count()} movimentações, "
            f"{PedidoCompra.objects.count()} pedidos."
        ))

    # ------------------------------------------------------------ usuários
    def _usuarios(self):
        for username, nome, papel in (("demo", "Visitante (demo)", Papel.OPERADOR), ("demo.leitura", "Visitante (leitura)", Papel.LEITURA)):
            u, criado = User.objects.get_or_create(username=username, defaults={"first_name": nome})
            if criado:
                u.set_unusable_password()
                u.save()
            Perfil.objects.update_or_create(user=u, defaults={"papel": papel})
        senha = settings.DEMO_ADMIN_PASSWORD
        if senha:
            admin, criado = User.objects.get_or_create(username="admin", defaults={"is_staff": True, "is_superuser": True, "first_name": "Administrador"})
            admin.is_staff = admin.is_superuser = True
            admin.set_password(senha)
            admin.save()
            Perfil.objects.update_or_create(user=admin, defaults={"papel": Papel.ADMIN})
            self.stdout.write("Usuário 'admin' configurado a partir de DEMO_ADMIN_PASSWORD.")

    def _limpar(self):
        with transaction.atomic():
            for m in (Movimentacao, ItemInventario, Inventario, ItemPedido, PedidoCompra, Produto, Categoria, Fornecedor, Deposito):
                m.objects.all().delete()

    # ------------------------------------------------------------ dados
    def _popular(self):
        rnd = random.Random(20251118)
        agora = timezone.now()
        usuario = User.objects.get(username="demo")
        outros = [usuario]
        fornecedores = [Fornecedor.objects.create(nome=n, cnpj=c, contato=ct, email=e, telefone=t, prazo_entrega_dias=p)
                        for n, c, ct, e, t, p in FORNECEDORES]
        deps = [Deposito.objects.create(codigo=c, nome=n, endereco=e) for c, n, e in DEPOSITOS]
        cd = deps[0]

        produtos, giro, minimo = [], {}, {}
        for catnome, (prefixo, fidx, margem, itens) in CATALOGO.items():
            cat = Categoria.objects.create(nome=catnome, descricao=f"Linha de {catnome.lower()}")
            for i, (nome, un, custo, taxa) in enumerate(itens, 1):
                custo = Decimal(str(custo))
                p = Produto(sku=f"{prefixo}-{i:04d}", nome=nome, categoria=cat, fornecedor=fornecedores[fidx], unidade=un,
                            custo=custo, preco=preco_de(float(custo), margem), criado_em=agora - timedelta(days=DIAS + 10))
                taxa_real = taxa * rnd.uniform(0.85, 1.15) * 0.6  # volume por dia (todas as unidades de venda)
                p.estoque_minimo = max(3, math.ceil(taxa_real * 10))
                produtos.append(p)
                giro[nome], minimo[nome] = taxa_real, p.estoque_minimo
        Produto.objects.bulk_create(produtos)
        produtos = list(Produto.objects.order_by("sku"))
        por_nome = {p.nome: p for p in produtos}
        dia = lambda d, h=None: (agora - timedelta(days=d)).replace(hour=h if h is not None else rnd.randint(8, 17), minute=rnd.randint(0, 59), second=0, microsecond=0)

        sem_giro = {por_nome[n].pk for n in SEM_GIRO}
        ruptura = {por_nome[n].pk for n in RUPTURA}
        saldo = defaultdict(int)  # (produto_id, deposito_id)
        movs = []

        def mov(p, dep, tipo, qtd, d, **kw):
            saldo[(p.pk, dep.pk)] += qtd
            movs.append(Movimentacao(produto=p, deposito=dep, tipo=tipo, quantidade=qtd, data=d, usuario=kw.pop("usuario", usuario),
                                     custo_unitario=p.custo, **kw))

        def total(p):
            return sum(saldo[(p.pk, d.pk)] for d in deps)

        # carga inicial
        for p in produtos:
            fator = 70 if p.pk in sem_giro else 26
            q = max(int(giro[p.nome] * fator), p.estoque_minimo * 3)
            mov(p, cd, Movimentacao.Tipo.ENTRADA, q, dia(DIAS + 1, 9), documento="Carga inicial", observacao="Saldo inicial migrado")
            for dep, peso in zip(deps[1:], PESO_DEP[1:]):
                t = int(q * peso * 0.6)
                if t >= 1:
                    tid = uuid.uuid4()
                    d0 = dia(DIAS, 10)
                    mov(p, cd, Movimentacao.Tipo.TRANSF_SAIDA, -t, d0, transferencia=tid, observacao=f"Para {dep.nome}")
                    mov(p, dep, Movimentacao.Tipo.TRANSF_ENTRADA, t, d0, transferencia=tid, observacao=f"De {cd.nome}")

        inv_ref = None
        chegadas = defaultdict(list)   # dia -> [(pedido, itens)]
        a_caminho = defaultdict(int)   # produto_id -> quantidade já pedida
        for d in range(DIAS, 0, -1):
            for ped, itens in chegadas.pop(d, []):
                for p, q in itens:
                    mov(p, cd, Movimentacao.Tipo.ENTRADA, q, ped.recebido_em, pedido=ped, documento=ped.numero,
                        observacao=f"Recebimento {ped.numero}")
                    a_caminho[p.pk] -= q
            data_dia = timezone.localtime(agora - timedelta(days=d))
            fator_dia = 0.45 if data_dia.weekday() == 6 else (0.75 if data_dia.weekday() == 5 else 1.0)
            tendencia = 0.85 + 0.3 * (DIAS - d) / DIAS  # crescimento gradual
            for p in produtos:
                if p.pk in sem_giro and d < 80:
                    continue
                if p.pk in sem_giro:
                    lam = giro[p.nome] * 0.5
                else:
                    lam = giro[p.nome] * fator_dia * tendencia
                q = max(0, int(round(rnd.gauss(lam, math.sqrt(lam) * 0.9))))
                if q == 0:
                    continue
                dep = rnd.choices(deps, weights=PESO_DEP)[0]
                if saldo[(p.pk, dep.pk)] < q:
                    dep = cd
                q = min(q, saldo[(p.pk, dep.pk)])
                if q <= 0:
                    continue
                if rnd.random() < 0.06:
                    mov(p, dep, Movimentacao.Tipo.SAIDA, -q, dia(d), motivo="consumo", observacao="Consumo interno")
                else:
                    desconto = Decimal("1") if rnd.random() < 0.8 else Decimal("0.95")
                    mov(p, dep, Movimentacao.Tipo.SAIDA, -q, dia(d), motivo="venda", preco_unitario=(p.preco * desconto).quantize(Decimal("0.01")),
                        documento=f"NF {rnd.randint(10000, 99999)}")

            if d % 7 == 0:
                grupos_sem = defaultdict(list)
                for p in produtos:
                    if p.pk in sem_giro and d < 100:
                        continue
                    if p.pk in ruptura and d < 45:
                        continue
                    ponto = int(giro[p.nome] * 14) + p.estoque_minimo
                    if total(p) + a_caminho[p.pk] <= ponto:
                        q = int(giro[p.nome] * rnd.randint(30, 42)) + p.estoque_minimo
                        grupos_sem[p.fornecedor_id].append((p, max(q, 6)))
                for fid, itens in grupos_sem.items():
                    forn = next(f for f in fornecedores if f.pk == fid)
                    if d - forn.prazo_entrega_dias < 2:
                        continue
                    criado = dia(d, 9)
                    ped = PedidoCompra.objects.create(
                        fornecedor=forn, deposito=cd, status=PedidoCompra.Status.RECEBIDO, criado_em=criado,
                        enviado_em=criado + timedelta(hours=3), previsao_entrega=(criado + timedelta(days=forn.prazo_entrega_dias)).date(),
                        recebido_em=dia(d - forn.prazo_entrega_dias, 7), usuario=usuario, observacao="Reposição periódica")
                    ItemPedido.objects.bulk_create(ItemPedido(pedido=ped, produto=p, quantidade=q, custo_unitario=p.custo) for p, q in itens)
                    for p, q in itens:
                        a_caminho[p.pk] += q
                    chegadas[d - forn.prazo_entrega_dias].append((ped, itens))
                # reposição das filiais por transferência
                for p in produtos:
                    for dep, peso in zip(deps[1:], PESO_DEP[1:]):
                        alvo = int(giro[p.nome] * peso * 18)
                        falta = alvo - saldo[(p.pk, dep.pk)]
                        if falta >= 3 and saldo[(p.pk, cd.pk)] > falta + p.estoque_minimo // 2 and p.pk not in sem_giro:
                            tid, d0 = uuid.uuid4(), dia(d, 15)
                            mov(p, cd, Movimentacao.Tipo.TRANSF_SAIDA, -falta, d0, transferencia=tid, observacao=f"Para {dep.nome}")
                            mov(p, dep, Movimentacao.Tipo.TRANSF_ENTRADA, falta, d0, transferencia=tid, observacao=f"De {cd.nome}")
            # perdas ocasionais
            if rnd.random() < 0.10:
                p = rnd.choice(produtos)
                if p.pk not in sem_giro and saldo[(p.pk, cd.pk)] > 3:
                    mov(p, cd, Movimentacao.Tipo.SAIDA, -rnd.randint(1, 3), dia(d), motivo="perda", observacao=rnd.choice(["Embalagem avariada", "Validade próxima", "Quebra no transporte"]))
            # inventário finalizado há 30 dias na filial norte
            if d == 30:
                inv_ref = (deps[1], dia(30, 17))

        movs.sort(key=lambda m: m.data)
        self._corrigir_negativos(movs, produtos, deps)

        # Estados finais forçados para a demonstração
        saldos_fim = defaultdict(int)
        for m in movs:
            saldos_fim[(m.produto_id, m.deposito_id)] += m.quantidade

        def drenar(p, alvo, d):
            """Consome o saldo até 'alvo', em várias saídas ao longo dos últimos dias (parece venda normal)."""
            atual = sum(saldos_fim[(p.pk, dp.pk)] for dp in deps)
            falta = atual - alvo
            partes = 6
            for k in range(partes):
                q_parte = math.ceil(falta / (partes - k)) if falta > 0 else 0
                for dp in [cd] + deps[1:]:
                    if q_parte <= 0:
                        break
                    q = min(q_parte, saldos_fim[(p.pk, dp.pk)])
                    if q > 0:
                        movs.append(Movimentacao(produto=p, deposito=dp, tipo="saida", motivo="venda", quantidade=-q, data=dia(1, 16 + k // 3),
                                                 usuario=usuario, custo_unitario=p.custo, preco_unitario=p.preco, documento=f"NF {rnd.randint(10000, 99999)}"))
                        saldos_fim[(p.pk, dp.pk)] -= q
                        falta -= q
                        q_parte -= q

        for n in RUPTURA:
            drenar(por_nome[n], 0, 1)
        for i, n in enumerate(ABAIXO_MIN):
            p = por_nome[n]
            drenar(p, max(1, int(p.estoque_minimo * (0.35 + 0.08 * i))), 1)

        # Inventário finalizado (filial norte, 30 dias atrás) com divergências em lançamentos de ajuste
        dep_inv, d_inv = inv_ref
        inv = Inventario.objects.create(deposito=dep_inv, status="finalizado", criado_em=d_inv - timedelta(hours=4), finalizado_em=d_inv,
                                        usuario=usuario, observacao="Contagem cíclica mensal")
        corrente = defaultdict(int)
        for m in movs:
            if m.data <= d_inv and m.deposito_id == dep_inv.pk:
                corrente[m.produto_id] += m.quantidade
        itens_inv, ajustes = [], 0
        for p in produtos:
            s = corrente[p.pk]
            if s <= 0:
                continue
            contada, dif = s, 0
            if rnd.random() < 0.18:
                dif = rnd.choice([-3, -2, -1, -1, 1, 2])
                dif = max(dif, -s)
                contada = s + dif
            itens_inv.append(ItemInventario(inventario=inv, produto=p, qtd_sistema=s, qtd_contada=contada, diferenca=dif))
            if dif:
                ajustes += 1
                movs.append(Movimentacao(produto=p, deposito=dep_inv, tipo="ajuste", quantidade=dif, data=d_inv, usuario=usuario,
                                         custo_unitario=p.custo, inventario=inv, observacao=f"Inventário {inv.numero}: contado {contada}, sistema {s}"))
        ItemInventario.objects.bulk_create(itens_inv)

        movs.sort(key=lambda m: m.data)
        self._corrigir_negativos(movs, produtos, deps)
        Movimentacao.objects.bulk_create(movs, batch_size=1000)

        # Pedidos em aberto: 2 rascunhos, 3 enviados (alguns com atraso), 1 cancelado
        base = {p.pk: sum(saldos_fim[(p.pk, dp.pk)] for dp in deps) for p in produtos}
        abaixo = [p for p in produtos if base[p.pk] <= p.estoque_minimo]
        grupos = defaultdict(list)
        for p in abaixo:
            grupos[p.fornecedor_id].append(p)
        status_seq = ["enviado", "enviado", "rascunho", "rascunho", "enviado"]
        for (fid, itens), st in zip(sorted(grupos.items(), key=lambda kv: -len(kv[1])), status_seq):
            forn = next(f for f in fornecedores if f.pk == fid)
            envio = dia(rnd.randint(1, 4), 10)
            ped = PedidoCompra.objects.create(
                fornecedor=forn, deposito=cd, status=st, usuario=usuario, criado_em=envio - timedelta(hours=5),
                enviado_em=envio if st == "enviado" else None,
                previsao_entrega=(envio + timedelta(days=forn.prazo_entrega_dias)).date() if st == "enviado" else None,
                observacao="Reposição de itens abaixo do mínimo")
            ItemPedido.objects.bulk_create(ItemPedido(pedido=ped, produto=p, quantidade=max(2 * p.estoque_minimo - base[p.pk], 6), custo_unitario=p.custo) for p in itens)
        forn = fornecedores[2]
        canc = PedidoCompra.objects.create(fornecedor=forn, deposito=cd, status="cancelado", usuario=usuario, criado_em=dia(20), observacao="Cancelado: preço fora da tabela")
        ItemPedido.objects.create(pedido=canc, produto=por_nome["Amaciante 2L"], quantidade=48, custo_unitario=por_nome["Amaciante 2L"].custo)

        # Inventário em contagem no CD (parcialmente lançado)
        inv2 = Inventario.objects.create(deposito=cd, status="aberto", criado_em=dia(0, 8), usuario=usuario, observacao="Contagem geral de fim de mês")
        itens2 = []
        for i, p in enumerate(sorted(produtos, key=lambda x: x.nome)):
            s = saldos_fim[(p.pk, cd.pk)]
            contada = None
            if i < 32:
                contada = s if rnd.random() > 0.2 else max(0, s + rnd.choice([-2, -1, 1, 2]))
            itens2.append(ItemInventario(inventario=inv2, produto=p, qtd_sistema=s, qtd_contada=contada))
        ItemInventario.objects.bulk_create(itens2)

    def _corrigir_negativos(self, movs, produtos, deps):
        """Garante que nenhum saldo (produto, depósito) fique negativo em ordem cronológica:
        se isso ocorrer, antecipa a data de uma entrada (recebimento) ou reduz a saída."""
        corrente = defaultdict(int)
        for m in movs:
            chave = (m.produto_id, m.deposito_id)
            if m.quantidade < 0 and corrente[chave] + m.quantidade < 0:
                m.quantidade = -max(corrente[chave], 0)
                if m.quantidade == 0:
                    m.quantidade = -1 if corrente[chave] > 0 else 0
            corrente[chave] += m.quantidade
        movs[:] = [m for m in movs if m.quantidade != 0]
        # transferências: manter par consistente (ajusta o lado da entrada ao da saída)
        saidas = {m.transferencia: m for m in movs if m.tipo == "transf_saida"}
        for m in movs:
            if m.tipo == "transf_entrada" and m.transferencia in saidas:
                m.quantidade = -saidas[m.transferencia].quantidade
