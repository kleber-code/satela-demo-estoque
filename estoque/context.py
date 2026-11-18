from django.conf import settings

from .models import Papel


def global_ctx(request):
    nivel = getattr(request, "nivel", 0)
    return {
        "DEMO_MODE": settings.DEMO_MODE,
        "papel": getattr(request, "papel", None),
        "pode_editar": nivel >= 2,
        "eh_admin": nivel >= 3,
        "PAPEL_ADMIN": Papel.ADMIN,
    }
