import json
import secrets
from datetime import timedelta
from django.contrib.auth import authenticate
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils import timezone

from .models import Alert, Check, Ping, AlertChannel
from .state import compute_next_scheduled_at


def get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def authenticate_write_token(request):
    """Authenticate for write operations - check API token in header or body"""
    token = request.headers.get('Authorization', '').replace('Bearer ', '').strip()
    if not token:
        token = request.headers.get('X-API-Token', '').strip()
    if not token and request.body:
        try:
            data = json.loads(request.body)
            token = data.get('api_token', '') or data.get('token', '')
        except Exception:
            pass
    return token


@csrf_exempt
@require_http_methods(["GET", "POST", "HEAD"])
def check_ping(request, check_uuid):
    try:
        check = Check.objects.get(uuid=check_uuid)
    except Check.DoesNotExist:
        return JsonResponse({'error': 'not found'}, status=404)
    
    if request.method == 'HEAD':
        return HttpResponse(status=200)
    
    ping_state = request.GET.get('state', 'success')
    if request.method == 'POST':
        try:
            if request.body:
                data = json.loads(request.body)
                ping_state = data.get('state', ping_state)
        except Exception:
            pass
    
    if ping_state not in ('success', 'failure'):
        ping_state = 'success'
    
    Ping.objects.create(
        monitored_check=check,
        state=ping_state,
        remote_addr=get_client_ip(request),
        user_agent=request.META.get('HTTP_USER_AGENT', '')[:255],
    )
    
    now = timezone.now()
    old_status = check.status
    
    if ping_state == 'failure':
        check.failure_count += 1
    else:
        check.success_count += 1
    
    check.last_ping = now
    check.next_scheduled_at = compute_next_scheduled_at(check)
    
    # On any ping, if we were late/down and pinged on time/recovered, go to up
    if check.status in (Check.STATUS_LATE, Check.STATUS_DOWN, Check.STATUS_NEW):
        check.status = Check.STATUS_UP
    elif check.status == Check.STATUS_UP:
        check.status = Check.STATUS_UP  # stay up on successful ping
    else:
        check.status = Check.STATUS_UP
    
    check.save(update_fields=['last_ping', 'next_scheduled_at', 'status', 'success_count', 'failure_count', 'updated_at'])
    
    return JsonResponse({
        'status': 'ok',
        'check_uuid': str(check.uuid),
        'state': ping_state,
        'status_now': check.status,
    })


@require_http_methods(["GET"])
def check_status(request, check_uuid):
    try:
        check = Check.objects.get(uuid=check_uuid)
    except Check.DoesNotExist:
        return JsonResponse({'error': 'not found'}, status=404)
    
    return JsonResponse({
        'uuid': str(check.uuid),
        'name': check.name,
        'status': check.status,
        'last_ping': check.last_ping.isoformat() if check.last_ping else None,
        'next_scheduled_at': check.next_scheduled_at.isoformat() if check.next_scheduled_at else None,
        'interval_seconds': check.interval_seconds,
        'grace_period_seconds': check.grace_period_seconds,
        'timeout_seconds': check.timeout_seconds,
        'success_count': check.success_count,
        'failure_count': check.failure_count,
    })


@csrf_exempt
@require_http_methods(["GET", "POST"])
def checks_list_create(request):
    if request.method == 'GET':
        token = authenticate_write_token(request)
        if not token:
            return JsonResponse({'error': 'unauthorized'}, status=401)
        # List checks that belong to this token? Or all - just require token
        checks = Check.objects.all()
        data = []
        for c in checks:
            data.append({
                'uuid': str(c.uuid),
                'name': c.name,
                'status': c.status,
                'interval_seconds': c.interval_seconds,
            })
        return JsonResponse({'checks': data})
    
    # POST - create
    token = authenticate_write_token(request)
    # For creation, we can be lenient or require token; make it token-authenticated as specified
    if not token:
        # Allow creation without token? The spec says "separate read/write API tokens per check; ... CRUD for checks (token-authenticated)"
        pass  # but enforce - let's check header
        token = request.headers.get('Authorization', '').replace('Bearer ', '').strip()
        if not token:
            return JsonResponse({'error': 'unauthorized'}, status=401)
    
    try:
        data = json.loads(request.body) if request.body else {}
    except Exception:
        data = {}
    
    try:
        check = Check.objects.create(
            name=data.get('name', 'Unnamed Check'),
            schedule_type=data.get('schedule_type', 'interval'),
            interval_seconds=int(data.get('interval_seconds', 300)),
            cron_spec=data.get('cron_spec', ''),
            grace_period_seconds=int(data.get('grace_period_seconds', 60)),
            timeout_seconds=int(data.get('timeout_seconds', 600)),
            api_token=token,
        )
    except Exception:
        existing = Check.objects.filter(api_token=token).first()
        if existing:
            for field in ['name', 'interval_seconds', 'grace_period_seconds', 'timeout_seconds', 'schedule_type', 'cron_spec']:
                if field in data and data.get(field) not in (None, ''):
                    try:
                        if field in ['interval_seconds', 'grace_period_seconds', 'timeout_seconds']:
                            setattr(existing, field, int(data.get(field)))
                        else:
                            setattr(existing, field, data.get(field))
                    except Exception:
                        pass
            existing.save()
            check = existing
        else:
            raise
    check.next_scheduled_at = compute_next_scheduled_at(check)
    check.save(update_fields=['next_scheduled_at'])
    
    return JsonResponse({
        'uuid': str(check.uuid),
        'name': check.name,
        'api_token': check.api_token,
        'status': check.status,
    }, status=201)


@csrf_exempt
@require_http_methods(["GET", "PUT", "DELETE"])
def check_detail(request, check_uuid):
    try:
        check = Check.objects.get(uuid=check_uuid)
    except Check.DoesNotExist:
        return JsonResponse({'error': 'not found'}, status=404)
    
    # Token auth for CRUD
    req_token = authenticate_write_token(request)
    if not req_token or (check.api_token and check.api_token != req_token):
        return JsonResponse({'error': 'unauthorized'}, status=401)
    
    if request.method == 'GET':
        return JsonResponse({
            'uuid': str(check.uuid),
            'name': check.name,
            'status': check.status,
            'interval_seconds': check.interval_seconds,
            'grace_period_seconds': check.grace_period_seconds,
            'timeout_seconds': check.timeout_seconds,
            'api_token': check.api_token,
        })
    elif request.method == 'PUT':
        try:
            data = json.loads(request.body) if request.body else {}
        except Exception:
            data = {}
        for field in ['name', 'interval_seconds', 'grace_period_seconds', 'timeout_seconds', 'enabled', 'cron_spec']:
            if field in data:
                setattr(check, field, data[field])
        check.save()
        return JsonResponse({'status': 'updated'})
    elif request.method == 'DELETE':
        check.delete()
        return JsonResponse({'status': 'deleted'})
    
    return JsonResponse({'error': 'bad request'}, status=400)


@require_http_methods(["GET"])
def summary(request):
    """The state of the instance: the checks, their state and the pings.

    The reading is open, the same way the ping and the status of a check are
    open: the uuid of a check is the secret of its job, and the checks
    themselves are what the console shows.
    """
    checks = list(Check.objects.all().order_by('created_at'))
    states = {status: 0 for status, _ in Check.STATUS_CHOICES}
    for check in checks:
        states[check.status] = states.get(check.status, 0) + 1

    pings = list(Ping.objects.select_related('monitored_check').all()[:20])
    alerts = list(Alert.objects.select_related('monitored_check').all()[:10])

    return JsonResponse({
        'stats': {
            'checks': len(checks),
            'up': states.get(Check.STATUS_UP, 0),
            'late': states.get(Check.STATUS_LATE, 0),
            'down': states.get(Check.STATUS_DOWN, 0),
        },
        'series': [
            {'label': status, 'value': states.get(status, 0)}
            for status, _ in Check.STATUS_CHOICES
        ],
        'lists': {
            'checks': [
                {
                    'name': check.name,
                    'status': check.status,
                    'interval': check.interval_seconds,
                    'grace': check.grace_period_seconds,
                    'last_ping': check.last_ping.isoformat() if check.last_ping else 'never',
                }
                for check in checks
            ],
            'pings': [
                {
                    'check': ping.monitored_check.name,
                    'state': ping.state,
                    'when': ping.created_at.isoformat(),
                }
                for ping in pings
            ],
            'alerts': [
                {
                    'check': alert.monitored_check.name,
                    'event': alert.event,
                    'channel': alert.channel_type or '-',
                    'success': 'yes' if alert.success else 'no',
                }
                for alert in alerts
            ],
        },
    })


@csrf_exempt
@require_http_methods(["POST", "DELETE"])
def session(request):
    """Sign in with the api token of a check, and sign out again.

    The api token of a check is the secret of the instance, so it is the
    credential the portal asks for. A user of the admin of the project can
    sign in with its name and its password as well.
    """
    if request.method == 'DELETE':
        request.session.flush()
        return JsonResponse({'ok': True})

    try:
        data = json.loads(request.body) if request.body else {}
    except Exception:
        data = {}
    token = str(data.get('token') or data.get('api_token') or '').strip()
    if token:
        check = Check.objects.filter(api_token=token).first()
        if not check:
            return JsonResponse({'error': 'the token is not known'}, status=401)
        request.session['api_token'] = token
        request.session['check_uuid'] = str(check.uuid)
        return JsonResponse({'user': {'name': check.name, 'role': 'owner'}})

    username = str(data.get('username') or '').strip()
    password = str(data.get('password') or '')
    user = authenticate(request, username=username, password=password)
    if user is None:
        return JsonResponse({'error': 'Wrong user name or password.'}, status=401)
    request.session['username'] = user.get_username()
    return JsonResponse({'user': {'name': user.get_username(), 'role': 'admin'}})
