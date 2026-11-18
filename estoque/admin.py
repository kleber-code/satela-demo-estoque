from django.contrib import admin

from . import models


@admin.register(models.Produto)
class ProdutoAdmin(admin.ModelAdmin):
    list_display = ("sku", "nome", "categoria", "custo", "preco", "estoque_minimo", "ativo")
    search_fields = ("sku", "nome")
    list_filter = ("categoria", "ativo")


@admin.register(models.Movimentacao)
class MovimentacaoAdmin(admin.ModelAdmin):
    """Somente leitura: o saldo só muda por movimentações registradas pelo sistema."""

    list_display = ("data", "produto", "deposito", "tipo", "quantidade", "usuario")
    list_filter = ("tipo", "deposito")
    search_fields = ("produto__sku", "produto__nome", "documento")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


for m in (models.Categoria, models.Fornecedor, models.Deposito, models.Perfil, models.PedidoCompra, models.Inventario):
    admin.site.register(m)
