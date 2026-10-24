from django.contrib import admin
from django.urls import path, re_path
from core import api, views

urlpatterns = [
    # The site of the project: the home page, the console, the portal and
    # the theme and the runtime of the site.
    path('', views.home, name='home'),
    path('console.html', views.console, name='console'),
    path('login.html', views.login_page, name='login'),
    re_path(r'^assets/(?P<path>.*)$', views.assets, name='assets'),

    path('admin/', admin.site.urls),
    path('api/v1/summary', api.summary, name='summary'),
    path('api/v1/session', api.session, name='session'),
    path('api/v1/checks', api.checks_list_create, name='checks_list_create'),
    path('api/v1/checks/<uuid:check_uuid>', api.check_detail, name='check_detail'),
    path('api/v1/checks/<uuid:check_uuid>/ping', api.check_ping, name='check_ping'),
    path('api/v1/checks/<uuid:check_uuid>/status', api.check_status, name='check_status'),
]
