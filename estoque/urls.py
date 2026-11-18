from django.urls import path

from . import views as v

urlpatterns = [
    path("saude/", v.saude, name="saude"),
    path("entrar/", v.EntrarView.as_view(), name="login"),
    path("demo/", v.demo, name="demo"),
    path("sair/", v.sair, name="logout"),
    path("", v.dashboard, name="dashboard"),

    path("produtos/", v.produto_lista, name="produto_lista"),
    path("produtos/novo/", v.produto_form, name="produto_novo"),
    path("produtos/<int:pk>/", v.produto_detalhe, name="produto_detalhe"),
    path("produtos/<int:pk>/editar/", v.produto_form, name="produto_editar"),
    path("produtos/<int:pk>/excluir/", v.produto_excluir, name="produto_excluir"),

    path("cadastros/<slug:slug>/", v.cadastro_lista, name="cadastro_lista"),
    path("cadastros/<slug:slug>/novo/", v.cadastro_form, name="cadastro_novo"),
    path("cadastros/<slug:slug>/<int:pk>/editar/", v.cadastro_form, name="cadastro_editar"),
    path("cadastros/<slug:slug>/<int:pk>/excluir/", v.cadastro_excluir, name="cadastro_excluir"),

    path("movimentacoes/", v.mov_lista, name="mov_lista"),
    path("movimentacoes/transferir/", v.transferencia_nova, name="transferencia_nova"),
    path("movimentacoes/nova/<str:tipo>/", v.mov_nova, name="mov_nova"),

    path("compras/", v.compra_lista, name="compra_lista"),
    path("compras/novo/", v.compra_form, name="compra_novo"),
    path("compras/<int:pk>/", v.compra_detalhe, name="compra_detalhe"),
    path("compras/<int:pk>/editar/", v.compra_form, name="compra_editar"),
    path("compras/<int:pk>/<str:acao>/", v.compra_acao, name="compra_acao"),

    path("alertas/", v.alertas, name="alertas"),
    path("alertas/gerar-compras/", v.alertas_gerar_compras, name="alertas_gerar_compras"),
    path("curva-abc/", v.curva_abc, name="curva_abc"),

    path("inventarios/", v.inventario_lista, name="inventario_lista"),
    path("inventarios/novo/", v.inventario_novo, name="inventario_novo"),
    path("inventarios/<int:pk>/", v.inventario_detalhe, name="inventario_detalhe"),

    path("usuarios/", v.usuarios, name="usuarios"),
    path("api/", v.api_docs, name="api_docs"),
    path("api/produtos/", v.api_produtos, name="api_produtos"),
    path("api/saldo/", v.api_saldo, name="api_saldo"),
]
