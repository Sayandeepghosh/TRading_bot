# Hosting this for free

Four options that genuinely cost nothing. The differences that matter are
whether the server sleeps, and whether strangers can reach it.

| | Always on | WebSockets | Card needed | Private | Notes |
|---|---|---|---|---|---|
| **Oracle Cloud Always Free** | yes | yes | yes, for identity only | yes | Real VM. The no-compromise option. |
| **Render free** | no, sleeps after 15 min | yes | no | yes | ~1 min cold start. Easiest setup. |
| **Hugging Face Spaces** | mostly | yes | no | **no, public** | Fine for a demo, wrong for real positions. |
| **Google Cloud Run** | no, scales to zero | yes | yes | yes | Generous request allowance, cold starts. |

Free tiers change. Oracle cut its ARM allocation from 4 OCPU/24 GB to
**2 OCPU/12 GB on 15 June 2026**, and Render cut free bandwidth to 5 GB/month in
April 2026. Both are still ample here. Check current limits before relying on
any of this.

---

## Option 1 — Oracle Cloud Always Free (recommended)

A real always-on VM: 2 ARM cores, 12 GB RAM, free forever, no sleep, no cold
starts. A card is required at signup for identity verification; Always Free
resources are not charged. Pick the **Ampere A1 (ARM)** shape, not the AMD
micro instances, and Ubuntu 22.04 or 24.04.

```bash
# --- on the VM, as the default 'ubuntu' user ---
sudo apt update && sudo apt install -y python3-venv python3-pip git

git clone https://github.com/YOUR_USER/YOUR_REPO.git TRading_bot
cd TRading_bot
./start.sh --help          # creates .venv and installs dependencies

# run it as a managed service
sudo cp deploy/analyser.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now analyser
journalctl -u analyser -f  # watch the first scan
```

The unit binds to `127.0.0.1` on purpose. Put Caddy in front for HTTPS and a
password, then open only 443:

```bash
# install Caddy (commands in deploy/Caddyfile), then
caddy hash-password --plaintext 'pick-something-strong'
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile      # set your domain and paste the hash
sudo systemctl reload caddy
```

Oracle blocks inbound ports twice, so you must open both:

```bash
# 1. the instance firewall
sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 80  -j ACCEPT
sudo netfilter-persistent save
```

2. In the OCI console: **Networking → Virtual Cloud Networks → your VCN →
Security Lists → Default** → add ingress rules for TCP 80 and 443 from
`0.0.0.0/0`. Forgetting this step is the single most common reason an Oracle VM
appears unreachable.

No domain? Use [Tailscale](https://tailscale.com) instead of Caddy. Free for
personal use, gives the VM a private address only your devices can reach, and
then `HOST=0.0.0.0` in the service file is safe because nothing else can route
to it.

---

## Option 2 — Render (easiest)

Push the repo, then in Render choose **New → Blueprint** and select it.
`render.yaml` configures everything.

The free service sleeps after 15 minutes without traffic and takes about a
minute to wake. Two things soften that:

- WebSocket messages count as inbound traffic, so an open dashboard tab keeps
  itself awake.
- The first request after waking triggers a background scan, and the page
  renders immediately with a progress bar rather than hanging.

The free plan has no persistent disk, so `.cache` is wiped on every cold start
and price history is re-downloaded. Correct, just slower.

---

## Option 3 — Hugging Face Spaces (no card)

See `deploy/huggingface-README.md`. Copy it to the Space as `README.md`; the
YAML front matter is what configures the Space.

**A free Space is public and this app has no login.** The settings page and the
holdings routes accept writes from anyone who opens it. Use it as a public demo,
never for real position tracking.

---

## Option 4 — Google Cloud Run

```bash
gcloud run deploy equity-analyser \
  --source . \
  --region asia-south1 \
  --allow-unauthenticated \
  --port 8000 \
  --memory 1Gi \
  --timeout 3600 \
  --set-env-vars TZ=Asia/Kolkata
```

Scales to zero, so you pay nothing when idle and accept a cold start when you
return. Drop `--allow-unauthenticated` and use IAM if you want it private.

---

## Any Docker host

```bash
docker build -t equity-analyser .
docker run -d --name analyser \
  -p 8000:8000 \
  -e TZ=Asia/Kolkata \
  -v analyser-cache:/app/.cache \
  --restart unless-stopped \
  equity-analyser
```

The named volume is worth it: it keeps the price cache across restarts, so
rebuilds do not re-download years of history.

---

## Security

Authentication is built in and **fails closed**: binding to anything other than
loopback turns it on. If you have not set a password, the app generates a random
one at boot and prints it to the logs rather than serving an unauthenticated
write API to the internet.

Set your own so it stays stable across restarts:

```bash
# in the platform dashboard, or the shell
ANALYSER_PASSWORD='something-strong'
ANALYSER_SECRET_KEY='...'            # signs sessions; without it logins drop on restart
```

On Render both are already declared in `render.yaml` — the password is prompted
at deploy time (`sync: false`, so it never enters git) and the secret key is
generated automatically.

Prefer a pre-hashed password if you would rather not put plaintext in a
dashboard:

```bash
python -m analyser.auth 'your-password'    # -> ANALYSER_PASSWORD_HASH
```

Caddy basic auth (`deploy/Caddyfile`) is still useful on your own VM as a second
layer and to keep unauthenticated traffic off the app entirely, but it is no
longer the only thing standing between the internet and your config.

To deliberately run without auth on a trusted private network:

```bash
ANALYSER_AUTH=off
```

## What "live" actually means here

Quotes refresh while the market is open, pushed over a WebSocket, and charts
update without a reload. The data source is Yahoo Finance, which runs roughly
**15 minutes behind the exchange**. No hosting choice changes that.

For true real-time you need a broker feed. A free Angel One SmartAPI account
provides real WebSocket ticks; implement it as another source in
`src/analyser/sources/` and point `LiveQuotes._fetch` at it. Nothing else has to
change, including the browser protocol.
