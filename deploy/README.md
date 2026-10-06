# Deploying me-public

The gated tools site, on the same droplet as the terminal. **me-app owns
127.0.0.1:8000 and `terminal.morningexecution.com`; this owns 127.0.0.1:8001
and `tools.morningexecution.com`.** Nothing is shared between them except nginx
and the box.

A commit is not a deploy here either. Push, pull on the box, restart, then
verify against the running server.

## What only you can do

Two steps need credentials or an account I cannot reach.

1. **The DNS A record.** `tools.morningexecution.com` did not resolve as of
   2026-10-06, and **certbot cannot issue a certificate until it does.** Add
   an A record pointing at the droplet's IPv4 address. The apex and `www` sit
   behind Cloudflare; this host must point **straight at the droplet** (grey
   cloud, DNS only) or the ACME HTTP challenge is answered by Cloudflare and
   fails.
2. **The three beehiiv values** in `/etc/me_public.env`. The signing secret is
   generated on the box by step 4, so only `BEEHIIV_API_KEY` and
   `BEEHIIV_PUBLICATION_ID` are typed by hand. Leave
   `BEEHIIV_PREMIUM_TIER_ID` unset until a paid tier exists: unset is safe,
   because `configured_levels()` fails closed and a Premium route denies with
   "not available yet". A wrong id would be worse than no id.

## Steps

Run as `deploy@` the droplet unless a line says `sudo`.

```bash
# 1. the code
sudo mkdir -p /opt/me-public && sudo chown deploy:deploy /opt/me-public
git clone https://github.com/tdumbauld/me-public.git /opt/me-public
cd /opt/me-public

# 2. its own venv. NOT me_app's: the two have different dependency sets and
#    sharing one makes an upgrade for either a risk to both.
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

# 3. the env file, owned by root and readable by the unit
sudo install -o root -g deploy -m 600 deploy/me_public.env.example /etc/me_public.env

# 4. the signing secret, written in place and never printed
sudo python3 - <<'PY'
import pathlib, re, secrets
p = pathlib.Path("/etc/me_public.env")
p.write_text(re.sub(r"(?m)^ME_PUBLIC_SECRET=.*$",
                    "ME_PUBLIC_SECRET=" + secrets.token_urlsafe(48),
                    p.read_text()))
PY

# 5. fill in the two beehiiv values by hand
sudo nano /etc/me_public.env

# 6. the unit
sudo cp deploy/me-public.service /etc/systemd/system/me-public.service
sudo systemctl daemon-reload
sudo systemctl enable --now me-public
systemctl is-active me-public

# 7. nginx. The limits file FIRST: the vhost references its zones, and nginx
#    refuses to load a vhost naming a zone that does not exist yet.
sudo cp deploy/me-public-limits.conf /etc/nginx/conf.d/me-public-limits.conf
sudo cp deploy/me-public.conf /etc/nginx/sites-available/me-public.conf
sudo ln -sfn /etc/nginx/sites-available/me-public.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# 8. the certificate, ONLY once the DNS record resolves
sudo certbot --nginx -d tools.morningexecution.com
```

## Verifying

Against the running server, not against git.

```bash
# the app itself, behind nginx
curl -s http://127.0.0.1:8001/api/health | python3 -m json.tool

# the gate: no session must be 401, never 200
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8001/tools/vol-crush   # 401
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8001/                  # 200

# and once DNS and the certificate are in place
curl -s https://tools.morningexecution.com/api/health
```

`/api/health` reports `levels_configured`. With no tier id set it reads
`["free"]`, which is the correct answer and not a fault.

## Traps

- **`levels_configured` is the deployment check that matters.** If it ever
  lists `premium` while no paid tier exists in beehiiv, the tier id in the env
  is wrong and every Premium route will deny every subscriber.
- **`SITE_URL` is baked into every magic link.** Set to the wrong origin, links
  are generated pointing at a host that cannot verify them, and the failure
  shows up as "that link has expired" rather than as a configuration error.
- **The subscriber cache is per process.** One uvicorn worker is deliberate; run
  more and each keeps its own, which costs a few extra beehiiv calls and
  nothing else. Said here because it is the one property that surprises people.
- **Restarting drops the cache and every consumed-link nonce.** Harmless: a
  lookup re-runs, and a replayed link inside its 15 minutes would still need
  the recipient's inbox.
- **Sessions survive a deploy, but not a secret rotation.** Rotating
  `ME_PUBLIC_SECRET` signs everyone out, which is the revocation handle; it is
  not something to do casually.
- **Do not add `auth_basic` to this vhost.** The terminal's vhost has it
  because that app has no auth of its own. This one's gate is in the
  application, and a basic-auth prompt here would lock out the subscribers the
  site exists to serve.
