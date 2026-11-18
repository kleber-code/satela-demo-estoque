from django import forms
from django.conf import settings
from django.forms import inlineformset_factory

from .models import (
    Categoria, Deposito, Fornecedor, ItemPedido, Movimentacao, PedidoCompra, Produto, Papel,
)


class EstiloMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for nome, campo in self.fields.items():
            w = campo.widget
            if isinstance(w, forms.CheckboxInput):
                w.attrs["class"] = "check"
            elif isinstance(w, forms.FileInput):
                w.attrs["class"] = "input"
            else:
                w.attrs["class"] = (w.attrs.get("class", "") + " input").strip()
            if isinstance(w, forms.NumberInput):
                w.attrs.setdefault("inputmode", "decimal")


class CategoriaForm(EstiloMixin, forms.ModelForm):
    class Meta:
        model = Categoria
        fields = ["nome", "descricao"]


class FornecedorForm(EstiloMixin, forms.ModelForm):
    class Meta:
        model = Fornecedor
        fields = ["nome", "cnpj", "contato", "email", "telefone", "prazo_entrega_dias", "ativo"]


class DepositoForm(EstiloMixin, forms.ModelForm):
    class Meta:
        model = Deposito
        fields = ["codigo", "nome", "endereco", "ativo"]


class ProdutoForm(EstiloMixin, forms.ModelForm):
    class Meta:
        model = Produto
        fields = ["sku", "nome", "categoria", "fornecedor", "unidade", "custo", "preco", "estoque_minimo", "foto", "ativo"]
        widgets = {"custo": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
                   "preco": forms.NumberInput(attrs={"step": "0.01", "min": "0"})}

    def clean_sku(self):
        return self.cleaned_data["sku"].strip().upper()

    def clean_foto(self):
        foto = self.cleaned_data.get("foto")
        if foto and hasattr(foto, "size") and foto.size > settings.FOTO_MAX_BYTES:
            raise forms.ValidationError("A foto deve ter no máximo 2 MB.")
        return foto


class MovimentacaoForm(EstiloMixin, forms.Form):
    """Entrada, saída e ajuste. A quantidade só é validada aqui; o saldo é checado no serviço."""

    produto = forms.ModelChoiceField(Produto.objects.none())
    deposito = forms.ModelChoiceField(Deposito.objects.none(), label="Depósito")
    quantidade = forms.IntegerField()
    custo_unitario = forms.DecimalField(label="Custo unitário (R$)", required=False, min_value=0, max_digits=12, decimal_places=2,
                                        widget=forms.NumberInput(attrs={"step": "0.01"}))
    preco_unitario = forms.DecimalField(label="Preço unitário (R$)", required=False, min_value=0, max_digits=12, decimal_places=2,
                                        widget=forms.NumberInput(attrs={"step": "0.01"}))
    motivo = forms.ChoiceField(choices=Movimentacao.Motivo.choices, required=False)
    documento = forms.CharField(label="Documento (NF / pedido)", max_length=40, required=False)
    observacao = forms.CharField(label="Observação", max_length=200, required=False)

    def __init__(self, *args, tipo="entrada", **kwargs):
        super().__init__(*args, **kwargs)
        self.tipo = tipo
        self.fields["produto"].queryset = Produto.objects.filter(ativo=True).order_by("nome")
        self.fields["deposito"].queryset = Deposito.objects.filter(ativo=True)
        if tipo == "ajuste":
            self.fields["quantidade"].label = "Ajuste (+ acrescenta, − reduz)"
            self.fields["observacao"].required = True
            self.fields["observacao"].label = "Justificativa"
        else:
            self.fields["quantidade"].min_value = 1
            self.fields["quantidade"].widget.attrs["min"] = 1
        if tipo != "entrada":
            del self.fields["custo_unitario"]
        if tipo != "saida":
            del self.fields["preco_unitario"]
            del self.fields["motivo"]
        else:
            self.fields["motivo"].initial = "venda"
            self.fields["motivo"].required = True
        # ModelChoiceField label amigável
        self.fields["produto"].label_from_instance = lambda p: f"{p.sku} · {p.nome} ({p.unidade})"
        self.fields["deposito"].label_from_instance = lambda d: f"{d.codigo} · {d.nome}"

    def clean_quantidade(self):
        q = self.cleaned_data["quantidade"]
        if q == 0:
            raise forms.ValidationError("Informe uma quantidade diferente de zero.")
        if self.tipo != "ajuste" and q < 0:
            raise forms.ValidationError("Informe uma quantidade positiva.")
        return q


class TransferenciaForm(EstiloMixin, forms.Form):
    produto = forms.ModelChoiceField(Produto.objects.none())
    origem = forms.ModelChoiceField(Deposito.objects.none(), label="Depósito de origem")
    destino = forms.ModelChoiceField(Deposito.objects.none(), label="Depósito de destino")
    quantidade = forms.IntegerField(min_value=1)
    observacao = forms.CharField(label="Observação", max_length=200, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["produto"].queryset = Produto.objects.filter(ativo=True).order_by("nome")
        deps = Deposito.objects.filter(ativo=True)
        self.fields["origem"].queryset = deps
        self.fields["destino"].queryset = deps
        self.fields["produto"].label_from_instance = lambda p: f"{p.sku} · {p.nome} ({p.unidade})"
        for f in ("origem", "destino"):
            self.fields[f].label_from_instance = lambda d: f"{d.codigo} · {d.nome}"

    def clean(self):
        dados = super().clean()
        if dados.get("origem") and dados.get("origem") == dados.get("destino"):
            self.add_error("destino", "Escolha um depósito diferente da origem.")
        return dados


class PedidoForm(EstiloMixin, forms.ModelForm):
    class Meta:
        model = PedidoCompra
        fields = ["fornecedor", "deposito", "previsao_entrega", "observacao"]
        widgets = {"previsao_entrega": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["fornecedor"].queryset = Fornecedor.objects.filter(ativo=True)
        self.fields["deposito"].queryset = Deposito.objects.filter(ativo=True)


class ItemPedidoForm(EstiloMixin, forms.ModelForm):
    custo_unitario = forms.DecimalField(label="Custo unit. (R$)", required=False, min_value=0, max_digits=12, decimal_places=2,
                                        widget=forms.NumberInput(attrs={"step": "0.01"}))

    class Meta:
        model = ItemPedido
        fields = ["produto", "quantidade", "custo_unitario"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["produto"].queryset = Produto.objects.filter(ativo=True).order_by("nome")
        self.fields["produto"].label_from_instance = lambda p: f"{p.sku} · {p.nome}"
        self.fields["quantidade"].widget.attrs["min"] = 1

    def clean(self):
        dados = super().clean()
        if dados.get("produto") and dados.get("custo_unitario") in (None, ""):
            dados["custo_unitario"] = dados["produto"].custo
            self.instance.custo_unitario = dados["produto"].custo
        return dados


def item_formset(extra=3):
    return inlineformset_factory(
        PedidoCompra, ItemPedido, form=ItemPedidoForm, extra=extra, can_delete=True, min_num=0
    )


class NovoInventarioForm(EstiloMixin, forms.Form):
    deposito = forms.ModelChoiceField(Deposito.objects.none(), label="Depósito")
    observacao = forms.CharField(label="Observação", max_length=200, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["deposito"].queryset = Deposito.objects.filter(ativo=True)


class PapelForm(forms.Form):
    papel = forms.ChoiceField(choices=Papel.choices)
