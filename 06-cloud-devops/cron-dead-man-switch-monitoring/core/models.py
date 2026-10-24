import uuid
from django.db import models
from django.utils import timezone


class Check(models.Model):
    STATUS_NEW = 'new'
    STATUS_UP = 'up'
    STATUS_LATE = 'late'
    STATUS_DOWN = 'down'
    STATUS_CHOICES = [
        (STATUS_NEW, 'New'),
        (STATUS_UP, 'Up'),
        (STATUS_LATE, 'Late'),
        (STATUS_DOWN, 'Down'),
    ]

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, blank=True, null=True)
    schedule_type = models.CharField(max_length=20, default='interval', choices=[('interval', 'Interval'), ('cron', 'Cron')])
    interval_seconds = models.PositiveIntegerField(default=300, blank=True, null=True)
    cron_spec = models.CharField(max_length=255, blank=True, null=True, help_text='Simplified cron-like: mon-sun,HH:MM')
    grace_period_seconds = models.PositiveIntegerField(default=60)
    timeout_seconds = models.PositiveIntegerField(default=600)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_NEW)
    last_ping = models.DateTimeField(blank=True, null=True)
    next_scheduled_at = models.DateTimeField(blank=True, null=True)
    success_count = models.PositiveIntegerField(default=0)
    failure_count = models.PositiveIntegerField(default=0)
    enabled = models.BooleanField(default=True)
    mute_until = models.DateTimeField(blank=True, null=True)
    api_token = models.CharField(max_length=255, unique=True, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def is_muted(self):
        if not self.mute_until:
            return False
        return timezone.now() < self.mute_until

    def __str__(self):
        return self.name


class Ping(models.Model):
    PING_SUCCESS = 'success'
    PING_FAILURE = 'failure'
    PING_STATE_CHOICES = [
        (PING_SUCCESS, 'Success'),
        (PING_FAILURE, 'Failure'),
    ]

    id = models.BigAutoField(primary_key=True)
    monitored_check = models.ForeignKey(Check, on_delete=models.CASCADE, related_name='pings')
    state = models.CharField(max_length=20, choices=PING_STATE_CHOICES, default=PING_SUCCESS)
    remote_addr = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.monitored_check.name}: {self.state}"


class AlertChannel(models.Model):
    CHANNEL_EMAIL = 'email'
    CHANNEL_WEBHOOK = 'webhook'
    CHANNEL_LOG = 'log'
    CHANNEL_CHOICES = [
        (CHANNEL_EMAIL, 'Email'),
        (CHANNEL_WEBHOOK, 'Webhook'),
        (CHANNEL_LOG, 'Log'),
    ]

    id = models.BigAutoField(primary_key=True)
    monitored_check = models.ForeignKey(Check, on_delete=models.CASCADE, related_name='alert_channels')
    channel_type = models.CharField(max_length=20, choices=CHANNEL_CHOICES)
    target = models.CharField(max_length=512, help_text='Email address, webhook URL, or log identifier')
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.monitored_check.name} - {self.channel_type}"


class Alert(models.Model):
    EVENT_NEW = 'new'
    EVENT_UP = 'up'
    EVENT_LATE = 'late'
    EVENT_DOWN = 'down'
    EVENT_RECOVERY = 'recovery'
    EVENT_CHOICES = [
        (EVENT_NEW, 'New'),
        (EVENT_UP, 'Up'),
        (EVENT_LATE, 'Late'),
        (EVENT_DOWN, 'Down'),
        (EVENT_RECOVERY, 'Recovery'),
    ]

    id = models.BigAutoField(primary_key=True)
    monitored_check = models.ForeignKey(Check, on_delete=models.CASCADE, related_name='alerts')
    event = models.CharField(max_length=20, choices=EVENT_CHOICES)
    channel_type = models.CharField(max_length=20, blank=True, null=True)
    target = models.CharField(max_length=512, blank=True, null=True)
    message = models.TextField(blank=True, null=True)
    success = models.BooleanField(default=True)
    error = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.monitored_check.name}: {self.event}"
