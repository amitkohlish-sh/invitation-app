import hmac
import json
import os
import secrets
import sqlite3
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from datetime import datetime

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('INVITATION_DATA', ROOT / 'data'))
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / 'invitations.sqlite3'
PASSWORD_FILE = DATA / 'organizer-password.txt'
if not PASSWORD_FILE.exists():
    descriptor = os.open(PASSWORD_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as output:
        output.write(secrets.token_urlsafe(18))
PASSWORD = os.environ.get('ORGANIZER_PASSWORD') or PASSWORD_FILE.read_text().strip()
SESSIONS = {}
ATTEMPTS = {}


def connect():
    database = sqlite3.connect(DB, timeout=15)
    database.row_factory = sqlite3.Row
    database.execute('PRAGMA foreign_keys=ON')
    return database


with connect() as database:
    database.executescript('''
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS events (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, date TEXT NOT NULL,
        location TEXT NOT NULL, description TEXT NOT NULL, host TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS guests (
        id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
        name TEXT NOT NULL, email TEXT NOT NULL, response TEXT NOT NULL DEFAULT 'Pending',
        note TEXT NOT NULL DEFAULT '', token TEXT NOT NULL UNIQUE, updated TEXT,
        UNIQUE(event_id, email)
    );
    ''')


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def send(self, status, payload, cookie=None):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        cookie = SimpleCookie(self.headers.get('Cookie', ''))
        session = cookie.get('session')
        return bool(session and SESSIONS.get(session.value, 0) > time.time())

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def do_PUT(self):
        self.dispatch()

    def do_DELETE(self):
        self.dispatch()

    def dispatch(self):
        try:
            self.route()
        except (ValueError, KeyError, TypeError):
            self.send(400, {'error': 'Please check the information and try again.'})
        except sqlite3.IntegrityError:
            self.send(409, {'error': 'This email is already on the guest list.'})
        except Exception:
            self.send(500, {'error': 'Something went wrong. Please try again.'})

    def route(self):
        path = urlsplit(self.path).path
        method = self.command
        if not path.startswith('/api/'):
            files = {'/': 'index.html', '/app.js': 'app.js', '/styles.css': 'styles.css'}
            filename = 'index.html' if path.startswith('/rsvp/') else files.get(path)
            if method != 'GET' or not filename:
                return self.send(404, {'error': 'Not found'})
            self.send_response(200)
            self.send_header('Content-Type', {'html': 'text/html; charset=utf-8', 'js': 'text/javascript', 'css': 'text/css'}[filename.split('.')[-1]])
            self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write((ROOT / filename).read_bytes())
            return
        payload = {}
        if method != 'GET':
            origin = self.headers.get('Origin')
            if origin and urlsplit(origin).netloc != self.headers.get('Host'):
                return self.send(403, {'error': 'Request origin rejected.'})
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                return self.send(415, {'error': 'JSON required.'})
            length = int(self.headers.get('Content-Length', 0))
            if length < 0 or length > 16384:
                return self.send(413, {'error': 'Request too large.'})
            payload = json.loads(self.rfile.read(length) or b'{}')
            if not isinstance(payload, dict):
                raise ValueError()
        if path == '/api/login' and method == 'POST':
            address = self.client_address[0]
            attempts = [stamp for stamp in ATTEMPTS.get(address, []) if stamp > time.time() - 300]
            ATTEMPTS[address] = attempts
            if len(attempts) >= 10:
                return self.send(429, {'error': 'Too many attempts. Try again in five minutes.'})
            if not hmac.compare_digest(str(payload.get('password', '')).encode(), PASSWORD.encode()):
                attempts.append(time.time())
                return self.send(401, {'error': 'Incorrect organizer password.'})
            token = secrets.token_urlsafe(32)
            SESSIONS[token] = time.time() + 43200
            secure = '; Secure' if os.environ.get('COOKIE_SECURE') == '1' else ''
            return self.send(200, {'ok': True}, f'session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200{secure}')
        if path == '/api/logout' and method == 'POST':
            cookie = SimpleCookie(self.headers.get('Cookie', ''))
            if cookie.get('session'):
                SESSIONS.pop(cookie['session'].value, None)
            return self.send(200, {'ok': True}, 'session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
        parts = path.strip('/').split('/')
        with connect() as database:
            if len(parts) == 3 and parts[1] == 'rsvp':
                guest = database.execute('SELECT * FROM guests WHERE token=?', (parts[2],)).fetchone()
                if not guest:
                    return self.send(404, {'error': 'This invitation is unavailable or has been removed.'})
                if method == 'PUT':
                    response = payload.get('response')
                    if response not in ['Accepted', 'Declined', 'Pending']:
                        raise ValueError()
                    note = self.field(payload, 'note', 1000, False)
                    database.execute('UPDATE guests SET response=?, note=?, updated=? WHERE id=?', (response, note, datetime.now().isoformat(), guest['id']))
                    return self.send(200, {'ok': True})
                if method != 'GET':
                    return self.send(405, {'error': 'Method not allowed'})
                event = database.execute('SELECT * FROM events WHERE id=?', (guest['event_id'],)).fetchone()
                return self.send(200, {'event': dict(event), 'guest': {key: guest[key] for key in ['name', 'response', 'note']}})
            if not self.authorized():
                return self.send(401, {'error': 'Please sign in to continue.'})
            if path == '/api/events' and method == 'GET':
                events = []
                for event in database.execute('SELECT * FROM events ORDER BY date'):
                    item = dict(event)
                    item['guests'] = [dict(guest) for guest in database.execute('SELECT * FROM guests WHERE event_id=? ORDER BY name COLLATE NOCASE', (event['id'],))]
                    events.append(item)
                return self.send(200, events)
            if parts[1] == 'events' and ((len(parts) == 2 and method == 'POST') or (len(parts) == 3 and method == 'PUT')):
                values = [self.field(payload, key, maximum, required) for key, maximum, required in [('title', 120, True), ('date', 30, True), ('location', 200, True), ('description', 3000, False), ('host', 120, True)]]
                datetime.fromisoformat(values[1])
                event_id = secrets.token_hex(12) if method == 'POST' else parts[2]
                if method == 'POST':
                    database.execute('INSERT INTO events VALUES (?,?,?,?,?,?)', [event_id] + values)
                else:
                    cursor = database.execute('UPDATE events SET title=?,date=?,location=?,description=?,host=? WHERE id=?', values + [event_id])
                    if not cursor.rowcount:
                        return self.send(404, {'error': 'Event not found'})
                return self.send(200, {'id': event_id})
            if len(parts) == 3 and parts[1] == 'events' and method == 'DELETE':
                database.execute('DELETE FROM events WHERE id=?', (parts[2],))
                return self.send(200, {'ok': True})
            if len(parts) == 4 and parts[1] == 'events' and parts[3] == 'guests' and method == 'POST':
                if not database.execute('SELECT id FROM events WHERE id=?', (parts[2],)).fetchone():
                    return self.send(404, {'error': 'Event not found'})
                name = self.field(payload, 'name', 120)
                email = self.field(payload, 'email', 254).lower()
                if '@' not in email or '.' not in email.rsplit('@', 1)[-1] or any(char.isspace() for char in email):
                    raise ValueError()
                database.execute('INSERT INTO guests(id,event_id,name,email,token) VALUES(?,?,?,?,?)', (secrets.token_hex(12), parts[2], name, email, secrets.token_urlsafe(32)))
                return self.send(201, {'ok': True})
            if len(parts) == 3 and parts[1] == 'guests' and method in ['DELETE', 'PUT']:
                if method == 'DELETE':
                    database.execute('DELETE FROM guests WHERE id=?', (parts[2],))
                else:
                    response = payload.get('response')
                    if response not in ['Pending', 'Accepted', 'Declined']:
                        raise ValueError()
                    database.execute('UPDATE guests SET response=?,updated=? WHERE id=?', (response, datetime.now().isoformat(), parts[2]))
                return self.send(200, {'ok': True})
        self.send(404, {'error': 'Not found'})

    def field(self, payload, key, maximum, required=True):
        value = payload.get(key, '')
        if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
            raise ValueError()
        return value.strip()


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    print(f'InvitationApp: http://localhost:{port}', flush=True)
    print(f'Organizer password file: {PASSWORD_FILE}', flush=True)
    ThreadingHTTPServer((os.environ.get('HOST', '127.0.0.1'), port), Handler).serve_forever()
