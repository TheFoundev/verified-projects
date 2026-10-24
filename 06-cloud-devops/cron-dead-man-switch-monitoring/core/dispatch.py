import json
import time
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from .models import Alert


def dispatch_alert(check, event, message=None):
    """Dispatch alerts to all enabled channels for a check, with alert-once-per-transition handled by caller."""
    if check.is_muted():
        return []
    
    message = message or f"Check '{check.name}' ({check.uuid}) transitioned to {event}"
    results = []
    
    channels = getattr(check, 'alert_channels').filter(enabled=True)
    # If no channels configured, still create log entries for visibility
    if not channels.exists():
        # Fall back to log channel
        class Dummy:
            channel_type = 'log'
            target = 'stdout'
        channels = [Dummy()]
    
    for ch in channels:
        try:
            if ch.channel_type == 'log':
                print(f"[DEADMAN ALERT] {timezone.now().isoformat()} | {check.name} | {event} | {message}")
                alert = Alert.objects.create(monitored_check=check, event=event, channel_type='log', target=ch.target, message=message, success=True)
                results.append(alert)
            elif ch.channel_type == 'email':
                try:
                    send_mail(
                        subject=f"[Deadman] {check.name} - {event}",
                        message=message,
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        recipient_list=[ch.target],
                        fail_silently=False,
                    )
                    alert = Alert.objects.create(monitored_check=check, event=event, channel_type='email', target=ch.target, message=message, success=True)
                    results.append(alert)
                except Exception as e:
                    alert = Alert.objects.create(monitored_check=check, event=event, channel_type='email', target=ch.target, message=message, success=False, error=str(e))
                    results.append(alert)
            elif ch.channel_type == 'webhook':
                payload = {
                    'check_uuid': str(check.uuid),
                    'check_name': check.name,
                    'event': event,
                    'message': message,
                    'status': check.status,
                    'timestamp': timezone.now().isoformat(),
                }
                data = json.dumps(payload).encode('utf-8')
                req = Request(ch.target, data=data, headers={'Content-Type': 'application/json'}, method='POST')
                max_attempts = 3
                backoff = [0.5, 1.0, 2.0]
                last_err = None
                for attempt in range(max_attempts):
                    try:
                        with urlopen(req, timeout=10) as resp:
                            resp.read()
                        alert = Alert.objects.create(monitored_check=check, event=event, channel_type='webhook', target=ch.target, message=message, success=True)
                        results.append(alert)
                        last_err = None
                        break
                    except Exception as e:
                        last_err = e
                        if attempt < max_attempts - 1:
                            time.sleep(backoff[attempt])
                if last_err is not None:
                    alert = Alert.objects.create(monitored_check=check, event=event, channel_type='webhook', target=ch.target, message=message, success=False, error=str(last_err))
                    results.append(alert)
            else:
                print(f"[DEADMAN ALERT-{ch.channel_type}] {check.name} | {event} | {message}")
                alert = Alert.objects.create(monitored_check=check, event=event, channel_type=ch.channel_type, target=getattr(ch, 'target', ''), message=message, success=True)
                results.append(alert)
        except Exception as e:
            alert = Alert.objects.create(monitored_check=check, event=event, channel_type=getattr(ch, 'channel_type', 'unknown'), target=getattr(ch, 'target', ''), message=message, success=False, error=str(e))
            results.append(alert)
    
    return results
