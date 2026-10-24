from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from core.models import Check, Ping, Alert, AlertChannel
import json


class PingEndpointTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.check = Check.objects.create(
            name='Test Check',
            interval_seconds=60,
            grace_period_seconds=30,
            timeout_seconds=300,
        )
    
    def test_ping_new_to_up(self):
        url = f'/api/v1/checks/{self.check.uuid}/ping'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.check.refresh_from_db()
        self.assertEqual(self.check.status, Check.STATUS_UP)
        self.assertIsNotNone(self.check.last_ping)
        self.assertEqual(self.check.success_count, 1)
    
    def test_ping_head(self):
        url = f'/api/v1/checks/{self.check.uuid}/ping'
        resp = self.client.head(url)
        self.assertEqual(resp.status_code, 200)
    
    def test_ping_post_with_state(self):
        url = f'/api/v1/checks/{self.check.uuid}/ping'
        resp = self.client.post(url, data=json.dumps({'state': 'failure'}), content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.check.refresh_from_db()
        self.assertEqual(self.check.status, Check.STATUS_UP)
        self.assertEqual(self.check.failure_count, 1)
    
    def test_ping_with_state_query(self):
        url = f'/api/v1/checks/{self.check.uuid}/ping?state=failure'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.check.refresh_from_db()
        self.assertEqual(self.check.failure_count, 1)


class StatusEndpointTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.check = Check.objects.create(name='Status Check', interval_seconds=120)
    
    def test_status(self):
        url = f'/api/v1/checks/{self.check.uuid}/status'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertIn('status', data)
        self.assertIn('last_ping', data)


class CrudAuthTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.token = 'testtoken123'
    
    def test_create_check_token_auth(self):
        resp = self.client.post('/api/v1/checks', 
                                data=json.dumps({'name': 'New Check', 'interval_seconds': 60}),
                                content_type='application/json',
                                HTTP_AUTHORIZATION=f'Bearer {self.token}')
        self.assertEqual(resp.status_code, 201)
        data = json.loads(resp.content)
        self.assertIn('uuid', data)
    
    def test_create_check_no_token_unauth(self):
        resp = self.client.post('/api/v1/checks',
                                data=json.dumps({'name': 'New Check'}),
                                content_type='application/json')
        self.assertEqual(resp.status_code, 401)
    
    def test_crud_requires_token(self):
        check = Check.objects.create(name='Protected', api_token='secret')
        url = f'/api/v1/checks/{check.uuid}'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 401)
        resp = self.client.get(url, HTTP_AUTHORIZATION='Bearer secret')
        self.assertEqual(resp.status_code, 200)
    
    def test_crud_update_delete(self):
        check = Check.objects.create(name='ToUpdate', api_token='tok')
        url = f'/api/v1/checks/{check.uuid}'
        resp = self.client.put(url, data=json.dumps({'name': 'Updated'}), 
                               content_type='application/json',
                               HTTP_AUTHORIZATION='Bearer tok')
        self.assertEqual(resp.status_code, 200)
        check.refresh_from_db()
        self.assertEqual(check.name, 'Updated')
        
        resp = self.client.delete(url, HTTP_AUTHORIZATION='Bearer tok')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Check.objects.filter(uuid=check.uuid).exists())
