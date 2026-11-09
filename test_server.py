import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request


class InvitationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        os.environ['INVITATION_DATA'] = cls.directory.name
        os.environ['ORGANIZER_PASSWORD'] = 'test-password'
        specification = importlib.util.spec_from_file_location('invitation_server', Path(__file__).with_name('server.py'))
        cls.module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(cls.module)
        cls.server = cls.module.ThreadingHTTPServer(('127.0.0.1', 0), cls.module.Handler)
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.directory.cleanup()

    def request(self, path, method='GET', payload=None, cookie=None, origin=None):
        headers = {'Content-Type': 'application/json'}
        if cookie:
            headers['Cookie'] = cookie
        if origin:
            headers['Origin'] = origin
        request = urllib.request.Request(self.url + path, data=json.dumps(payload).encode() if payload is not None else None, method=method, headers=headers)
        try:
            response = urllib.request.urlopen(request)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read()), response.headers

    def test_complete_workflow(self):
        self.assertEqual(self.request('/api/events')[0], 401)
        self.assertEqual(self.request('/api/login', 'POST', {'password': 'wrong'})[0], 401)
        status, _, headers = self.request('/api/login', 'POST', {'password': 'test-password'})
        self.assertEqual(status, 200)
        cookie = headers['Set-Cookie'].split(';')[0]
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        event = {'title': 'Garden dinner', 'date': '2027-04-20T18:00', 'location': 'The garden', 'description': 'Dinner with friends', 'host': 'Amit'}
        self.assertEqual(self.request('/api/events', 'POST', event, cookie, 'https://untrusted.example')[0], 403)
        self.assertEqual(self.request('/api/events', 'POST', {**event, 'title': ' '}, cookie)[0], 400)
        self.assertEqual(self.request('/api/events', 'POST', {**event, 'date': 'invalid'}, cookie)[0], 400)
        status, result, _ = self.request('/api/events', 'POST', event, cookie)
        self.assertEqual(status, 200)
        event_id = result['id']
        guest = {'name': 'Jamie', 'email': 'Jamie@example.com'}
        self.assertEqual(self.request(f'/api/events/{event_id}/guests', 'POST', guest, cookie)[0], 201)
        self.assertEqual(self.request(f'/api/events/{event_id}/guests', 'POST', {**guest, 'email': 'jamie@example.com'}, cookie)[0], 409)
        _, events, _ = self.request('/api/events', cookie=cookie)
        saved_guest = next(item for item in events if item['id'] == event_id)['guests'][0]
        token = saved_guest['token']
        status, invitation, _ = self.request('/api/rsvp/' + token)
        self.assertEqual(status, 200)
        self.assertNotIn('email', invitation['guest'])
        self.assertEqual(self.request('/api/rsvp/' + token, 'PUT', {'response': 'Accepted', 'note': 'Vegetarian, please'})[0], 200)
        self.assertEqual(self.request('/api/rsvp/' + token, 'PUT', {'response': 'Invalid'})[0], 400)
        with self.module.connect() as database:
            saved = database.execute('SELECT response,note FROM guests WHERE id=?', (saved_guest['id'],)).fetchone()
            self.assertEqual(tuple(saved), ('Accepted', 'Vegetarian, please'))
        event['title'] = 'Garden supper'
        self.assertEqual(self.request('/api/events/' + event_id, 'PUT', event, cookie)[0], 200)
        self.assertEqual(self.request('/api/rsvp/' + token)[1]['event']['title'], 'Garden supper')
        self.assertEqual(self.request('/api/guests/' + saved_guest['id'], 'PUT', {'response': 'Declined'}, cookie)[0], 200)
        self.assertEqual(self.request('/api/events/' + event_id, 'DELETE', {}, cookie)[0], 200)
        self.assertEqual(self.request('/api/rsvp/' + token)[0], 404)
        self.assertEqual(self.request('/api/logout', 'POST', {}, cookie)[0], 200)
        self.assertEqual(self.request('/api/events', cookie=cookie)[0], 401)


if __name__ == '__main__':
    unittest.main()
