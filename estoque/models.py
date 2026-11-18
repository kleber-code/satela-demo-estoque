import secrets
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Max, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone


class Papel(models.TextChoices):
    LEITURA = "leitura", "Somente leitura"
    OPERADOR = "operador", "Operador"
    ADMIN = "admin", "Administrador"


NIVEL = {Papel.LEITURA: 1, Papel.OPERADOR: 2, Papel.ADMIN: 3}


class Perfil(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="perfil")
    papel = models.CharField(max_length=10, choices=Papel.choices, default=Papel.LEITURA)

    def __str__(self):
        return f"{self.user.username} ({self.get_papel_display()})"


def papel_do_usuario(user):
    if not user.is_authenticated:
        return None
    if user.is_superuser:
        return Papel.ADMIN
    perfil = Perfil.objects.filter(user=user).first()
    return Papel(perfil.papel) if perfil else Papel.LEITURA


class ApiToken(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="api_token")
    chave = models.CharField(max_length=64, unique=True, editable=False)
    criado_em = models.DateTimeField(default=timezone.now)

    @classmethod
    def gerar(cls, user):
        cls.objects.filter(user=user).delete()
        return cls.objects.create(user=user, chave=secrets.token_hex(24))


class Categoria(models.Model):
    nome = models.CharField(max_length=80, unique=True)
    descricao = models.CharField("descrição", max_length=200, blank=True)

    class Meta:
        ordering = ["nome"]

    def __str__(self):
        return self.nome


class Fornecedor(models.Model):
    nome = models.CharField(max_length=120)
    cnpj = models.CharField("CNPJ", max_length=18, blank=True)
    contato = models.CharField(max_length=80, blank=True)
    email = models.EmailField("e-mail", blank=True)
    telefone = models.CharField(max_length=20, blank=True)
    prazo_entrega_dias = models.PositiveIntegerField("prazo de entrega (dias)", default=7)
    ativo = models.BooleanField(default=True)

    class Meta:
        ordering = ["nome"]
        verbose_name_plural = "fornecedores"

    def __str__(self):
        return self.nome


class Deposito(models.Model):
    codigo = models.CharField("código", max_length=10, unique=True)
    nome = models.CharField(max_length=80)
    endereco = models.CharField("endereço", max_length=160, blank=True)
    ativo = models.BooleanField(default=True)

    class Meta:
        ordering = ["codigo"]
        verbose_name = "depósito"

    def __str__(self):
        return f"{self.codigo} · {self.nome}"


class ProdutoQuerySet(models.QuerySet):
    def com_saldo(self, deposito=None):
        """Anota o saldo (soma das movimentações) e a data da última saída.

        O saldo NUNCA é um campo editável: é sempre calculado a partir das movimentações.
        """
        qs = self.annotate(
            saldo=Coalesce(Sum("movimentacoes__quantidade"), 0),
            ultima_saida=Max("movimentacoes__data", filter=Q(movimentacoes__tipo=Movimentacao.Tipo.SAIDA)),
        )
        if deposito is not None:
            qs = qs.annotate(
                saldo_dep=Coalesce(Sum("movimentacoes__quantidade", filter=Q(movimentacoes__deposito=deposito)), 0)
            )
        return qs.annotate(
            valor_estoque=models.ExpressionWrapper(
                F("saldo") * F("custo"), output_field=models.DecimalField(max_digits=16, decimal_places=2)
            )
        )

    def filtrar_situacao(self, situacao):
        """Requer com_saldo(). situacao: ruptura | minimo | ok | sem_giro."""
        corte = timezone.now() - timedelta(days=settings.DIAS_SEM_GIRO)
        if situacao == "ruptura":
            return self.filter(saldo__lte=0)
        if situacao == "minimo":
            return self.filter(saldo__gt=0, saldo__lte=F("estoque_minimo"))
        if situacao == "ok":
            return self.filter(saldo__gt=F("estoque_minimo"))
        if situacao == "sem_giro":
            return self.filter(saldo__gt=0).filter(Q(ultima_saida__isnull=True) | Q(ultima_saida__lt=corte))
        return self


class Produto(models.Model):
    class Unidade(models.TextChoices):
        UN = "UN", "Unidade (UN)"
        CX = "CX", "Caixa (CX)"
        PCT = "PCT", "Pacote (PCT)"
        FD = "FD", "Fardo (FD)"
        KG = "KG", "Quilo (KG)"
        L = "L", "Litro (L)"
        RL = "RL", "Rolo (RL)"
        RM = "RM", "Resma (RM)"

    sku = models.CharField("SKU", max_length=20, unique=True)
    nome = models.CharField(max_length=140)
    categoria = models.ForeignKey(Categoria, on_delete=models.PROTECT, related_name="produtos")
    fornecedor = models.ForeignKey(
        Fornecedor, on_delete=models.SET_NULL, null=True, blank=True, related_name="produtos",
        verbose_name="fornecedor padrão",
    )
    unidade = models.CharField(max_length=3, choices=Unidade.choices, default=Unidade.UN)
    custo = models.DecimalField("custo (R$)", max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    preco = models.DecimalField("preço de venda (R$)", max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    estoque_minimo = models.PositiveIntegerField("estoque mínimo", default=0)
    foto = models.ImageField(upload_to="produtos/", blank=True, null=True)
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(default=timezone.now)

    objects = ProdutoQuerySet.as_manager()

    class Meta:
        ordering = ["nome"]

    def __str__(self):
        return f"{self.sku} · {self.nome}"

    @property
    def margem(self):
        if not self.preco:
            return Decimal("0")
        return (self.preco - self.custo) / self.preco * 100

    @property
    def situacao(self):
        """Requer anotações de com_saldo()."""
        saldo = getattr(self, "saldo", None)
        if saldo is None:
            return "ok"
        if saldo <= 0:
            return "ruptura"
        if saldo <= self.estoque_minimo:
            return "minimo"
        corte = timezone.now() - timedelta(days=settings.DIAS_SEM_GIRO)
        ultima = getattr(self, "ultima_saida", None)
        if ultima is None or ultima < corte:
            return "sem_giro"
        return "ok"

    @property
    def iniciais(self):
        palavras = [p for p in self.nome.split() if p[0].isalnum()]
        return "".join(p[0] for p in palavras[:2]).upper()

    @property
    def cor_idx(self):
        return (self.categoria_id or 0) % 8 + 1


class Movimentacao(models.Model):
    """Registro imutável de movimentação. quantidade é assinada: + entra, - sai."""

    class Tipo(models.TextChoices):
        ENTRADA = "entrada", "Entrada (compra)"
        SAIDA = "saida", "Saída"
        AJUSTE = "ajuste", "Ajuste"
        TRANSF_SAIDA = "transf_saida", "Transferência (saída)"
        TRANSF_ENTRADA = "transf_entrada", "Transferência (entrada)"

    class Motivo(models.TextChoices):
        VENDA = "venda", "Venda"
        CONSUMO = "consumo", "Consumo interno"
        PERDA = "perda", "Perda / avaria"

    produto = models.ForeignKey(Produto, on_delete=models.PROTECT, related_name="movimentacoes")
    deposito = models.ForeignKey(Deposito, on_delete=models.PROTECT, related_name="movimentacoes", verbose_name="depósito")
    tipo = models.CharField(max_length=15, choices=Tipo.choices, db_index=True)
    motivo = models.CharField(max_length=10, choices=Motivo.choices, blank=True)
    quantidade = models.IntegerField(help_text="Assinada: positiva entra, negativa sai.")
    custo_unitario = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    preco_unitario = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    data = models.DateTimeField(default=timezone.now, db_index=True)
    usuario = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    documento = models.CharField(max_length=40, blank=True)
    observacao = models.CharField("observação", max_length=200, blank=True)
    transferencia = models.UUIDField(null=True, blank=True, db_index=True)
    pedido = models.ForeignKey("PedidoCompra", on_delete=models.SET_NULL, null=True, blank=True, related_name="movimentacoes")
    inventario = models.ForeignKey("Inventario", on_delete=models.SET_NULL, null=True, blank=True, related_name="ajustes")

    class Meta:
        ordering = ["-data", "-id"]
        verbose_name = "movimentação"
        verbose_name_plural = "movimentações"
        indexes = [models.Index(fields=["produto", "data"])]

    def __str__(self):
        return f"{self.get_tipo_display()} {self.quantidade:+d} · {self.produto.sku}"

    @property
    def entrada(self):
        return self.quantidade > 0

    @property
    def valor(self):
        base = self.custo_unitario if self.custo_unitario is not None else self.produto.custo
        return abs(self.quantidade) * base


class PedidoCompra(models.Model):
    class Status(models.TextChoices):
        RASCUNHO = "rascunho", "Rascunho"
        ENVIADO = "enviado", "Enviado"
        RECEBIDO = "recebido", "Recebido"
        CANCELADO = "cancelado", "Cancelado"

    fornecedor = models.ForeignKey(Fornecedor, on_delete=models.PROTECT, related_name="pedidos")
    deposito = models.ForeignKey(Deposito, on_delete=models.PROTECT, verbose_name="depósito de destino")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RASCUNHO, db_index=True)
    criado_em = models.DateTimeField(default=timezone.now)
    enviado_em = models.DateTimeField(null=True, blank=True)
    previsao_entrega = models.DateField("previsão de entrega", null=True, blank=True)
    recebido_em = models.DateTimeField(null=True, blank=True)
    usuario = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    observacao = models.CharField("observação", max_length=200, blank=True)

    class Meta:
        ordering = ["-criado_em", "-id"]
        verbose_name = "pedido de compra"
        verbose_name_plural = "pedidos de compra"

    @property
    def numero(self):
        return f"PC-{self.pk:05d}" if self.pk else "novo"

    def __str__(self):
        return self.numero

    @property
    def total(self):
        return sum((i.subtotal for i in self.itens.all()), Decimal("0"))

    @property
    def editavel(self):
        return self.status == self.Status.RASCUNHO


class ItemPedido(models.Model):
    pedido = models.ForeignKey(PedidoCompra, on_delete=models.CASCADE, related_name="itens")
    produto = models.ForeignKey(Produto, on_delete=models.PROTECT)
    quantidade = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    custo_unitario = models.DecimalField("custo unitário (R$)", max_digits=12, decimal_places=2)

    @property
    def subtotal(self):
        return self.quantidade * self.custo_unitario


class Inventario(models.Model):
    class Status(models.TextChoices):
        ABERTO = "aberto", "Em contagem"
        FINALIZADO = "finalizado", "Finalizado"

    deposito = models.ForeignKey(Deposito, on_delete=models.PROTECT, verbose_name="depósito")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ABERTO)
    criado_em = models.DateTimeField(default=timezone.now)
    finalizado_em = models.DateTimeField(null=True, blank=True)
    usuario = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    observacao = models.CharField("observação", max_length=200, blank=True)

    class Meta:
        ordering = ["-criado_em", "-id"]
        verbose_name = "inventário"
        verbose_name_plural = "inventários"

    @property
    def numero(self):
        return f"INV-{self.pk:04d}" if self.pk else "novo"

    def __str__(self):
        return self.numero


class ItemInventario(models.Model):
    inventario = models.ForeignKey(Inventario, on_delete=models.CASCADE, related_name="itens")
    produto = models.ForeignKey(Produto, on_delete=models.PROTECT)
    qtd_sistema = models.IntegerField("saldo no sistema")
    qtd_contada = models.IntegerField("contagem física", null=True, blank=True)
    diferenca = models.IntegerField(null=True, blank=True)

    class Meta:
        unique_together = [("inventario", "produto")]
        ordering = ["produto__nome"]
