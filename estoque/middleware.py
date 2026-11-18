from django.shortcuts import redirect
from django.utils.http import urlencode

from .models import NIVEL, papel_do_usuario

LIVRES = ("/saude/", "/entrar/", "/demo/", "/static/", "/api/", "/admin/", "/favicon.ico")


class AcessoMiddleware:
    """Exige login em todo o sistema (exceto saúde, login, demo, estáticos e API por token)
    e anexa o papel do usuário à requisição."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        request.papel = papel_do_usuario(user)
        request.nivel = NIVEL.get(request.papel, 0)
        if not user.is_authenticated and not request.path.startswith(LIVRES):
            return redirect("/entrar/?" + urlencode({"next": request.get_full_path()}))
        return self.get_response(request)
