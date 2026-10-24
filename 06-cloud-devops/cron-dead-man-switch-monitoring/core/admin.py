from django.contrib import admin
from .models import Check, Ping, AlertChannel, Alert


@admin.register(Check)
class CheckAdmin(admin.ModelAdmin):
    list_display = ('name', 'uuid', 'status', 'interval_seconds', 'grace_period_seconds', 'timeout_seconds', 'last_ping', 'enabled')
    list_filter = ('status', 'enabled')
    search_fields = ('name', 'uuid')


@admin.register(Ping)
class PingAdmin(admin.ModelAdmin):
    list_display = ('monitored_check', 'state', 'created_at', 'remote_addr')
    list_filter = ('state',)
    search_fields = ('monitored_check__name',)


@admin.register(AlertChannel)
class AlertChannelAdmin(admin.ModelAdmin):
    list_display = ('monitored_check', 'channel_type', 'target', 'enabled')
    list_filter = ('channel_type', 'enabled')


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ('monitored_check', 'event', 'channel_type', 'success', 'created_at')
    list_filter = ('event', 'channel_type', 'success')
    search_fields = ('monitored_check__name',)
