# InvitationApp

A responsive invitation workspace with a Python server, SQLite persistence, organizer authentication, and private guest RSVP pages. Requires Python 3.10+; no packages to install.

## Start

```sh
python3 server.py
```

Open http://localhost:8000. Sign in using the generated password in `data/organizer-password.txt`. You may instead set `ORGANIZER_PASSWORD` before starting. Data is saved in `data/invitations.sqlite3`. Stop the server before copying the data folder for a simple backup.

Create an event, add guests, and use **Copy link** to share each private invitation. Guests can accept or decline and leave a note without organizer access. Refresh the dashboard to retrieve their replies. Organizers can edit/delete events, remove guests, update responses, and search/filter guests. Restarting the server preserves events and guests but signs organizers out.

## Network and deployment

By default the server listens only on `127.0.0.1`. For a trusted local network, start with `HOST=0.0.0.0 python3 server.py` and access it through the server's LAN address before copying guest links. A localhost link only works on the same computer. Public guest access requires deployment at a reachable HTTPS URL, a reverse proxy, `COOKIE_SECURE=1`, a persistent private data directory, and a strong `ORGANIZER_PASSWORD`. This bundled standard-library server is intended for local use, not hardened public hosting. `PORT` and `INVITATION_DATA` are configurable.

## Scope

- Single organizer workspace with password sign-in and expiring, HttpOnly sessions.
- SQLite with foreign keys, duplicate-guest prevention, and cascading deletion.
- Unique random RSVP links authorize responses for one guest; treat them as private.
- Server validation, parameterized SQL, same-origin mutation checks, and escaped rendering.
- Event date/time is a local wall-clock time, without timezone conversion.
- Email delivery is not configured; copy and send links through your preferred channel.
- The old browser-local prototype's data is not automatically imported.

Run the integration checks with `python3 -m unittest test_server.py`.
