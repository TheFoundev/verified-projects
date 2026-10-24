"""The web layer of the project: the home page, the console and the assets.

The pages of the site are the templates of the project and the theme and the
runtime of the site are served from `static/assets`.
"""
from django.conf import settings
from django.shortcuts import render
from django.views.static import serve


def home(request):
    """The home page of the site."""
    return render(request, "index.html")


def console(request):
    """The console of the site: the live panels and the numbers."""
    return render(request, "console.html")


def login_page(request):
    """The sign-in portal of the site."""
    return render(request, "login.html")


def assets(request, path):
    """The theme and the runtime of the site."""
    return serve(request, path, document_root=settings.BASE_DIR / "static" / "assets")
