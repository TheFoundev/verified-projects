import time
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Check
from core.state import check_deadline_exceeded, should_alert_on_transition, transition_check
from core.dispatch import dispatch_alert


class Command(BaseCommand):
    help = 'Background loop evaluating due checks → transitions → alerts'

    def add_arguments(self, parser):
        parser.add_argument('--sleep', type=int, default=5, help='Sleep seconds between polls')
        parser.add_argument('--once', action='store_true', help='Run once then exit')

    def handle(self, *args, **options):
        sleep_sec = options['sleep']
        once = options['once']
        
        self.stdout.write(self.style.SUCCESS('Starting dead-man-switch poller...'))
        
        while True:
            now = timezone.now()
            # Find checks that might be due
            checks = Check.objects.filter(enabled=True)
            for check in checks:
                result = check_deadline_exceeded(check)
                if result:  # late or down
                    old_status = check.status
                    if result == 'late' and check.status not in (Check.STATUS_LATE, Check.STATUS_DOWN):
                        _, new_status = transition_check(check, Check.STATUS_LATE)
                        if should_alert_on_transition(old_status, new_status):
                            dispatch_alert(check, 'late', f"Check '{check.name}' is late")
                    elif result == 'down' and check.status != Check.STATUS_DOWN:
                        _, new_status = transition_check(check, Check.STATUS_DOWN)
                        if should_alert_on_transition(old_status, new_status):
                            dispatch_alert(check, 'down', f"Check '{check.name}' is down (timeout exceeded)")
                # For checks that are currently late/down but received a ping, recovery is handled in ping endpoint
            if once:
                break
            time.sleep(sleep_sec)
        
        self.stdout.write(self.style.SUCCESS('Poller finished'))
