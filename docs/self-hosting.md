# Self-hosting Delta Neutral Monitor

This guide describes the recommended production setup for this repo:

- FastAPI backend on a VPS
- systemd for process supervision
- nginx reverse proxy on port 80
- Next.js dashboard on Vercel
- Vercel configured to call the VPS backend through `MONITOR_API_BASE_URL`

## 1. Backend prerequisites

On the VPS, install:

- Python 3.9+
- git
- nginx
- Node.js only if you also build locally on the server (not required for backend-only hosting)

Clone the repo and install Python dependencies:

```bash
git clone https://github.com/truzhenikov/delta-neutral-monitor.git
cd delta-neutral-monitor
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Create runtime config:

```bash
cp .env.example .env
```

Fill in the real exchange credentials.

## 2. Local backend verification

Before touching systemd or nginx, verify the backend locally:

```bash
uvicorn src.main:app --host 0.0.0.0 --port 8080
```

In another shell:

```bash
curl http://127.0.0.1:8080/health
curl http://127.0.0.1:8080/v1/status
curl http://127.0.0.1:8080/v1/history
```

Do not proceed until these work.

## 3. Install the systemd units

Copy the sample unit files:

```bash
sudo cp deploy/delta-neutral-monitor-backend.service /etc/systemd/system/
sudo cp deploy/delta-neutral-monitor-history.service /etc/systemd/system/
```

Reload systemd and enable the backend:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now delta-neutral-monitor-backend
```

Check status:

```bash
sudo systemctl status delta-neutral-monitor-backend --no-pager
```

### History snapshots

The repo currently includes a oneshot service for capturing history snapshots:

- `delta-neutral-monitor-history.service`

You can trigger it manually:

```bash
sudo systemctl start delta-neutral-monitor-history
```

If you want scheduled snapshots, add a matching timer unit in your own environment or trigger the script from cron/systemd timer.

## 4. Put nginx in front of the backend

Example `/etc/nginx/sites-available/delta-neutral-monitor`:

```nginx
server {
    listen 80;
    server_name _;

    location /health {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /v1/ {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable it:

```bash
sudo ln -sf /etc/nginx/sites-available/delta-neutral-monitor /etc/nginx/sites-enabled/delta-neutral-monitor
sudo nginx -t
sudo systemctl reload nginx
```

Open the firewall for HTTP if needed:

```bash
sudo ufw allow 80/tcp
```

Now verify from outside the VPS:

```bash
curl http://YOUR_SERVER_IP/health
curl http://YOUR_SERVER_IP/v1/status
```

## 5. Connect the Vercel dashboard

In the Vercel project for `webapp`, set:

```bash
MONITOR_API_BASE_URL=http://YOUR_SERVER_IP
```

Redeploy the webapp.

The Next.js routes under `webapp/app/api/` proxy to the backend and intentionally do not serve demo data.

## 6. Expected stale-data behavior

If one connector fails but others still work:

- the backend reuses the latest cached snapshot for the failed venue
- `/v1/status` returns `source: "stale"`
- the UI marks stale exchanges and shows the snapshot timestamp

If the entire backend is unreachable:

- the Vercel proxy route returns `502`
- the dashboard keeps the last successfully loaded client-side snapshot for the current browser session
- no fake/demo portfolio is injected

## 7. Recommended checks after every deploy

Backend:

```bash
curl http://127.0.0.1:8080/health
curl http://127.0.0.1:8080/v1/status
```

Public backend:

```bash
curl http://YOUR_SERVER_IP/health
curl http://YOUR_SERVER_IP/v1/status
```

Frontend:

- open the Vercel production URL
- verify the page renders
- verify the dashboard says `Live data` or `Stale cached data`
- verify it does **not** say demo fallback

## 8. Troubleshooting

### The frontend shows a backend error
Check:

1. `MONITOR_API_BASE_URL` in Vercel
2. nginx is running
3. backend service is running
4. firewall allows inbound HTTP

### One venue is stale
That usually means:

- connector timeout
- upstream exchange outage
- credential issue
- IP whitelist issue

Inspect:

```bash
sudo journalctl -u delta-neutral-monitor-backend -n 200 --no-pager
```

### The project shows old values
If the UI says stale, that is expected behavior during connector failure. The correct fix is to restore the connector, not to add a demo fallback.
