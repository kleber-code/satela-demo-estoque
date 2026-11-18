from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import services
from .models import (
    ApiToken, Categoria, Deposito, Fornecedor, Inventario, ItemInventario, ItemPedido, Movimentacao, Papel,
    PedidoCompra, Perfil, Produto,
)
from .services import ErroEstoque


def criar_usuario(nome, papel):
    u = User.objects.create_user(nome, password="senha-de-teste-123")
    Perfil.objects.create(user=u, papel=papel)
    return u


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cat = Categoria.objects.create(nome="Mercearia")
        cls.forn = Fornecedor.objects.create(nome="Fornecedor Teste", prazo_entrega_dias=5)
        cls.cd = Deposito.objects.create(codigo="CD01", nome="CD")
        cls.fn = Deposito.objects.create(codigo="FN02", nome="Filial")
        cls.p = Produto.objects.create(sku="T-1", nome="Produto Um", categoria=cls.cat, fornecedor=cls.forn,
                                       custo=Decimal("10.00"), preco=Decimal("15.00"), estoque_minimo=10)
        cls.admin = criar_usuario("adm", Papel.ADMIN)
        cls.op = criar_usuario("op", Papel.OPERADOR)
        cls.leitor = criar_usuario("leitor", Papel.LEITURA)

    def saldo(self, dep=None):
        return services.saldo(self.p, dep)


class SaldoEKardexTests(Base):
    def test_saldo_vem_das_movimentacoes(self):
        services.registrar_entrada(self.p, self.cd, 50, self.op)
        services.registrar_saida(self.p, self.cd, 12, self.op)
        services.registrar_ajuste(self.p, self.cd, -3, self.op, obs="quebra")
        self.assertEqual(self.saldo(), 35)
        anotado = Produto.objects.com_saldo().get(pk=self.p.pk)
        self.assertEqual(anotado.saldo, 35)
        self.assertEqual(anotado.valor_estoque, Decimal("350.00"))

    def test_saida_nao_pode_exceder_saldo_do_deposito(self):
        services.registrar_entrada(self.p, self.cd, 5, self.op)
        with self.assertRaises(ErroEstoque):
            services.registrar_saida(self.p, self.cd, 6, self.op)
        with self.assertRaises(ErroEstoque):
            services.registrar_saida(self.p, self.fn, 1, self.op)  # saldo é por depósito
        self.assertEqual(self.saldo(), 5)

    def test_ajuste_negativo_nao_deixa_saldo_negativo(self):
        with self.assertRaises(ErroEstoque):
            services.registrar_ajuste(self.p, self.cd, -1, self.op, obs="x")

    def test_quantidades_invalidas(self):
        with self.assertRaises(ErroEstoque):
            services.registrar_entrada(self.p, self.cd, 0, self.op)
        with self.assertRaises(ErroEstoque):
            services.registrar_saida(self.p, self.cd, -2, self.op)

    def test_kardex_saldo_corrente(self):
        t0 = timezone.now() - timedelta(days=3)
        services.registrar_entrada(self.p, self.cd, 100, self.op, data=t0)
        services.registrar_saida(self.p, self.cd, 30, self.op, data=t0 + timedelta(days=1))
        services.registrar_entrada(self.p, self.fn, 20, self.op, data=t0 + timedelta(days=2))
        linhas = services.kardex(self.p)  # mais recente primeiro
        self.assertEqual([m.saldo_corrente for m in linhas], [90, 70, 100])
        so_cd = services.kardex(self.p, self.cd)
        self.assertEqual([m.saldo_corrente for m in so_cd], [70, 100])
        self.assertEqual(linhas[0].saldo_corrente, self.saldo())

    def test_tela_do_produto_mostra_kardex(self):
        services.registrar_entrada(self.p, self.cd, 8, self.op)
        self.client.force_login(self.leitor)
        r = self.client.get(reverse("produto_detalhe", args=[self.p.pk]))
        self.assertContains(r, "Kardex")
        self.assertContains(r, "+8")


class TransferenciaTests(Base):
    def test_transferir_move_saldo_e_mantem_total(self):
        services.registrar_entrada(self.p, self.cd, 40, self.op)
        saida, entrada = services.transferir(self.p, self.cd, self.fn, 15, self.op)
        self.assertEqual(self.saldo(self.cd), 25)
        self.assertEqual(self.saldo(self.fn), 15)
        self.assertEqual(self.saldo(), 40)
        self.assertEqual(saida.transferencia, entrada.transferencia)
        self.assertEqual((saida.quantidade, entrada.quantidade), (-15, 15))

    def test_transferencia_invalida(self):
        services.registrar_entrada(self.p, self.cd, 10, self.op)
        with self.assertRaises(ErroEstoque):
            services.transferir(self.p, self.cd, self.cd, 1, self.op)
        with self.assertRaises(ErroEstoque):
            services.transferir(self.p, self.cd, self.fn, 11, self.op)
        self.assertEqual(Movimentacao.objects.count(), 1)

    def test_transferencia_pela_tela(self):
        services.registrar_entrada(self.p, self.cd, 10, self.op)
        self.client.force_login(self.op)
        r = self.client.post(reverse("transferencia_nova"), {"produto": self.p.pk, "origem": self.cd.pk, "destino": self.fn.pk, "quantidade": 4})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.saldo(self.fn), 4)


class CompraTests(Base):
    def _pedido(self, status=PedidoCompra.Status.RASCUNHO):
        ped = PedidoCompra.objects.create(fornecedor=self.forn, deposito=self.cd, status=status, usuario=self.op)
        ItemPedido.objects.create(pedido=ped, produto=self.p, quantidade=30, custo_unitario=Decimal("11.50"))
        return ped

    def test_fluxo_rascunho_enviado_recebido_gera_entradas(self):
        ped = self._pedido()
        services.enviar_pedido(ped, self.op)
        ped.refresh_from_db()
        self.assertEqual(ped.status, "enviado")
        self.assertIsNotNone(ped.previsao_entrega)
        self.assertEqual(self.saldo(), 0)  # enviar não mexe no estoque
        services.receber_pedido(ped, self.op)
        ped.refresh_from_db()
        self.assertEqual(ped.status, "recebido")
        self.assertEqual(self.saldo(self.cd), 30)
        mov = Movimentacao.objects.get(pedido=ped)
        self.assertEqual((mov.tipo, mov.quantidade, mov.custo_unitario), ("entrada", 30, Decimal("11.50")))
        self.p.refresh_from_db()
        self.assertEqual(self.p.custo, Decimal("11.50"))

    def test_recebimento_nao_duplica(self):
        ped = self._pedido(PedidoCompra.Status.ENVIADO)
        services.receber_pedido(ped, self.op)
        with self.assertRaises(ErroEstoque):
            services.receber_pedido(ped, self.op)
        self.assertEqual(self.saldo(), 30)

    def test_nao_recebe_rascunho_nem_envia_vazio(self):
        ped = self._pedido()
        with self.assertRaises(ErroEstoque):
            services.receber_pedido(ped, self.op)
        vazio = PedidoCompra.objects.create(fornecedor=self.forn, deposito=self.cd)
        with self.assertRaises(ErroEstoque):
            services.enviar_pedido(vazio)

    def test_cancelado_nao_recebe(self):
        ped = self._pedido(PedidoCompra.Status.ENVIADO)
        services.cancelar_pedido(ped)
        with self.assertRaises(ErroEstoque):
            services.receber_pedido(ped, self.op)

    def test_recebimento_pela_tela(self):
        ped = self._pedido(PedidoCompra.Status.ENVIADO)
        self.client.force_login(self.op)
        self.client.post(reverse("compra_acao", args=[ped.pk, "receber"]))
        self.assertEqual(self.saldo(), 30)

    def test_criar_pedido_com_itens_pela_tela(self):
        self.client.force_login(self.op)
        dados = {
            "fornecedor": self.forn.pk, "deposito": self.cd.pk, "observacao": "teste",
            "itens-TOTAL_FORMS": 1, "itens-INITIAL_FORMS": 0, "itens-MIN_NUM_FORMS": 0, "itens-MAX_NUM_FORMS": 1000,
            "itens-0-produto": self.p.pk, "itens-0-quantidade": 7, "itens-0-custo_unitario": "",
        }
        r = self.client.post(reverse("compra_novo"), dados)
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["formset"].errors)
        ped = PedidoCompra.objects.get()
        self.assertEqual(ped.itens.get().custo_unitario, Decimal("10.00"))  # padrão: custo do produto


class AlertaTests(Base):
    def setUp(self):
        self.p2 = Produto.objects.create(sku="T-2", nome="Dois", categoria=self.cat, fornecedor=self.forn, custo=1, preco=2, estoque_minimo=5)
        self.p3 = Produto.objects.create(sku="T-3", nome="Três", categoria=self.cat, fornecedor=self.forn, custo=1, preco=2, estoque_minimo=5)
        self.p4 = Produto.objects.create(sku="T-4", nome="Quatro", categoria=self.cat, fornecedor=self.forn, custo=2, preco=3, estoque_minimo=5)

    def test_classificacao(self):
        agora = timezone.now()
        # p (T-1): abaixo do mínimo (saldo 8 <= 10) e com saída recente
        services.registrar_entrada(self.p, self.cd, 20, self.op, data=agora - timedelta(days=5))
        services.registrar_saida(self.p, self.cd, 12, self.op)
        # p2: ruptura (entrou e saiu tudo)
        services.registrar_entrada(self.p2, self.cd, 4, self.op, data=agora - timedelta(days=5))
        services.registrar_saida(self.p2, self.cd, 4, self.op)
        # p3: saudável e com giro
        services.registrar_entrada(self.p3, self.cd, 100, self.op, data=agora - timedelta(days=5))
        services.registrar_saida(self.p3, self.cd, 1, self.op)
        # p4: sem giro (saída há 100 dias, saldo parado)
        services.registrar_entrada(self.p4, self.cd, 30, self.op, data=agora - timedelta(days=150))
        services.registrar_saida(self.p4, self.cd, 2, self.op, data=agora - timedelta(days=100))
        skus = lambda t: [p.sku for p in services.produtos_em_alerta(t)]
        self.assertEqual(skus("minimo"), ["T-1"])
        self.assertEqual(skus("ruptura"), ["T-2"])
        self.assertEqual(skus("sem_giro"), ["T-4"])
        self.assertEqual(services.contagem_alertas(), {"minimo": 1, "ruptura": 1, "sem_giro": 1})
        self.assertEqual(Produto.objects.com_saldo().get(pk=self.p3.pk).situacao, "ok")

    def test_sugestao_e_geracao_de_rascunho(self):
        services.registrar_entrada(self.p, self.cd, 4, self.op)  # abaixo do mínimo
        criados = services.gerar_sugestoes_compra(self.op)
        self.assertEqual(len(criados), 1)
        item = criados[0].itens.get(produto=self.p)
        self.assertEqual(item.quantidade, 2 * 10 - 4)
        # segunda chamada não duplica pedidos em aberto
        self.assertEqual(services.gerar_sugestoes_compra(self.op), [])

    def test_curva_abc(self):
        services.registrar_entrada(self.p, self.cd, 1000, self.op)
        services.registrar_entrada(self.p2, self.cd, 1000, self.op)
        services.registrar_saida(self.p, self.cd, 90, self.op)   # 90 x 10 = 900
        services.registrar_saida(self.p2, self.cd, 10, self.op)  # 10 x 1 = 10
        linhas, resumo, total = services.curva_abc()
        classe = {l["produto"].sku: l["classe"] for l in linhas}
        self.assertEqual(classe["T-1"], "A")
        self.assertEqual(classe["T-3"], "C")  # sem consumo
        self.assertEqual(total, Decimal("910"))
        self.assertAlmostEqual(float(sum(l["pct"] for l in linhas)), 100.0, places=3)

    def test_valor_total_do_estoque(self):
        services.registrar_entrada(self.p, self.cd, 10, self.op)   # 10 x 10
        services.registrar_entrada(self.p4, self.cd, 5, self.op)   # 5 x 2
        total = sum(p.valor_estoque for p in Produto.objects.com_saldo())
        self.assertEqual(total, Decimal("110"))


class InventarioTests(Base):
    def test_contagem_gera_ajustes(self):
        services.registrar_entrada(self.p, self.cd, 20, self.op)
        inv = services.abrir_inventario(self.cd, self.op)
        item = inv.itens.get(produto=self.p)
        self.assertEqual(item.qtd_sistema, 20)
        item.qtd_contada = 17
        item.save()
        n = services.finalizar_inventario(inv, self.op)
        self.assertEqual(n, 1)
        self.assertEqual(self.saldo(self.cd), 17)
        aj = Movimentacao.objects.get(inventario=inv)
        self.assertEqual((aj.tipo, aj.quantidade), ("ajuste", -3))
        with self.assertRaises(ErroEstoque):
            services.finalizar_inventario(inv, self.op)

    def test_nao_abre_dois_inventarios_no_mesmo_deposito(self):
        services.abrir_inventario(self.cd, self.op)
        with self.assertRaises(ErroEstoque):
            services.abrir_inventario(self.cd, self.op)

    def test_lancar_contagem_pela_tela(self):
        services.registrar_entrada(self.p, self.cd, 9, self.op)
        inv = services.abrir_inventario(self.cd, self.op)
        item = inv.itens.get(produto=self.p)
        self.client.force_login(self.op)
        r = self.client.post(reverse("inventario_detalhe", args=[inv.pk]), {f"c_{item.pk}": "11", "acao": "finalizar"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.saldo(self.cd), 11)
        self.assertEqual(Inventario.objects.get(pk=inv.pk).status, "finalizado")


class PermissaoTests(Base):
    def test_anonimo_vai_para_login_mas_saude_e_livre(self):
        r = self.client.get(reverse("produto_lista"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("/entrar/", r["Location"])
        r = self.client.get("/saude/")
        self.assertEqual((r.status_code, r.content), (200, b"ok"))

    def test_leitura_consulta_mas_nao_altera(self):
        self.client.force_login(self.leitor)
        for nome in ("dashboard", "produto_lista", "mov_lista", "compra_lista", "alertas", "curva_abc", "inventario_lista"):
            self.assertEqual(self.client.get(reverse(nome)).status_code, 200, nome)
        self.assertEqual(self.client.get(reverse("mov_nova", args=["entrada"])).status_code, 403)
        r = self.client.post(reverse("mov_nova", args=["entrada"]), {"produto": self.p.pk, "deposito": self.cd.pk, "quantidade": 5})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(Movimentacao.objects.count(), 0)
        self.assertEqual(self.client.get(reverse("produto_novo")).status_code, 403)
        self.assertEqual(self.client.post(reverse("alertas_gerar_compras")).status_code, 403)
        self.assertEqual(self.client.get(reverse("usuarios")).status_code, 403)

    def test_operador_lanca_movimentacao_mas_nao_exclui_nem_gerencia_usuarios(self):
        self.client.force_login(self.op)
        r = self.client.post(reverse("mov_nova", args=["entrada"]), {"produto": self.p.pk, "deposito": self.cd.pk, "quantidade": 5})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.saldo(), 5)
        self.assertEqual(self.client.post(reverse("produto_excluir", args=[self.p.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("usuarios")).status_code, 403)

    def test_saida_pela_tela_com_saldo_insuficiente_mostra_erro(self):
        self.client.force_login(self.op)
        r = self.client.post(reverse("mov_nova", args=["saida"]), {"produto": self.p.pk, "deposito": self.cd.pk, "quantidade": 5, "motivo": "venda"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Saldo insuficiente")
        self.assertEqual(Movimentacao.objects.count(), 0)

    def test_admin_altera_papel(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("usuarios")).status_code, 200)
        self.client.post(reverse("usuarios"), {"user_id": self.leitor.pk, "papel": "operador"})
        self.assertEqual(Perfil.objects.get(user=self.leitor).papel, "operador")

    def test_produto_com_historico_e_inativado_ao_excluir(self):
        services.registrar_entrada(self.p, self.cd, 1, self.op)
        self.client.force_login(self.admin)
        self.client.post(reverse("produto_excluir", args=[self.p.pk]))
        self.p.refresh_from_db()
        self.assertFalse(self.p.ativo)


class ExportacaoEApiTests(Base):
    def setUp(self):
        services.registrar_entrada(self.p, self.cd, 10, self.op)
        self.client.force_login(self.leitor)

    def test_csv_produtos(self):
        r = self.client.get(reverse("produto_lista"), {"export": "csv"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/csv", r["Content-Type"])
        self.assertIn("T-1", r.content.decode("utf-8-sig"))

    def test_csv_neutraliza_formulas(self):
        Produto.objects.filter(pk=self.p.pk).update(nome="=HYPERLINK(\"x\")")
        r = self.client.get(reverse("produto_lista"), {"export": "csv"})
        self.assertIn("'=HYPERLINK", r.content.decode("utf-8-sig"))

    def test_paginacao_e_busca(self):
        for i in range(30):
            Produto.objects.create(sku=f"P-{i:02d}", nome=f"Item {i}", categoria=self.cat, custo=1, preco=2)
        r = self.client.get(reverse("produto_lista"), {"page": 2})
        self.assertEqual(len(r.context["pagina"]), 11)
        r = self.client.get(reverse("produto_lista"), {"q": "Item 7"})
        self.assertEqual(r.context["total"], 1)

    def test_api_exige_token(self):
        self.client.logout()
        self.assertEqual(self.client.get("/api/produtos/").status_code, 401)
        self.assertEqual(self.client.get("/api/produtos/", HTTP_AUTHORIZATION="Bearer errado").status_code, 401)

    def test_api_produtos_e_saldo(self):
        tok = ApiToken.gerar(self.leitor)
        h = {"HTTP_AUTHORIZATION": f"Bearer {tok.chave}"}
        self.client.logout()
        r = self.client.get("/api/produtos/", **h)
        self.assertEqual(r.status_code, 200)
        dados = r.json()
        self.assertEqual(dados["count"], 1)
        self.assertEqual(dados["results"][0]["saldo"], 10)
        r = self.client.get("/api/saldo/", {"sku": "T-1"}, **h).json()
        self.assertEqual(r["results"], [{"sku": "T-1", "produto": "Produto Um", "deposito": "CD01", "deposito_nome": "CD", "saldo": 10}])
        self.assertEqual(self.client.get("/api/saldo/", {"sku": "nada"}, **h).status_code, 404)
        self.assertEqual(self.client.post("/api/produtos/", **h).status_code, 200)  # método não altera nada (somente leitura)


@override_settings(DEMO_MODE=True)
class DemoELoginTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_entrar_na_demonstracao_vira_operador(self):
        r = self.client.post("/demo/")
        self.assertEqual(r.status_code, 302)
        r = self.client.get("/")
        self.assertContains(r, "Ambiente de demonstração")
        self.assertContains(r, "Nova entrada")
        self.assertFalse(User.objects.get(username="demo").has_usable_password())

    def test_demo_leitura(self):
        self.client.post("/demo/", {"papel": "leitura"})
        self.assertEqual(self.client.get(reverse("mov_nova", args=["entrada"])).status_code, 403)

    @override_settings(DEMO_MODE=False)
    def test_demo_desligado(self):
        self.assertEqual(self.client.post("/demo/").status_code, 404)

    @override_settings(LOGIN_MAX_TENTATIVAS=3)
    def test_rate_limit_no_login(self):
        User.objects.create_user("alguem", password="correta-123456")
        for _ in range(3):
            r = self.client.post("/entrar/", {"username": "alguem", "password": "errada"})
            self.assertEqual(r.status_code, 200)
        r = self.client.post("/entrar/", {"username": "alguem", "password": "correta-123456"})
        self.assertEqual(r.status_code, 429)

    def test_login_normal(self):
        User.objects.create_user("alguem", password="correta-123456")
        r = self.client.post("/entrar/", {"username": "alguem", "password": "correta-123456"})
        self.assertEqual(r.status_code, 302)


class SeedTests(TestCase):
    def test_seed_idempotente_e_sem_admin_padrao(self):
        from django.core.management import call_command
        call_command("seed_demo", verbosity=0)
        n = Movimentacao.objects.count()
        self.assertGreaterEqual(Produto.objects.count(), 80)
        call_command("seed_demo", "--se-vazio", verbosity=0)
        self.assertEqual(Movimentacao.objects.count(), n)
        self.assertFalse(User.objects.filter(is_superuser=True).exists())
        self.assertTrue(all(c > 0 for c in services.contagem_alertas().values()))
        # nenhum saldo negativo em nenhum depósito
        from django.db.models import Sum
        negativos = Movimentacao.objects.values("produto", "deposito").annotate(s=Sum("quantidade")).filter(s__lt=0)
        self.assertEqual(negativos.count(), 0)
