# OpenGenerate v2.0 — Operator Manual & Production Runbook

> **Production Safety Rule:** Never execute destructive database operations or cutover migrations without a verified backup and explicit operator confirmation.

---

## 1. Service Start, Stop, Restart & Status

### Docker Compose (Recommended Production Path)
```bash
# 1. Validate configuration before starting
docker compose run --rm app check-config

# 2. Apply idempotent Alembic PostgreSQL migrations
docker compose run --rm migrate

# 3. Start or update the service stack
docker compose up -d --build

# 4. Inspect container status and structured JSON logs
docker compose ps
docker compose logs -f --tail=100 app

# 5. Graceful stop / restart (waits up to 30s for in-flight operations to settle)
docker compose restart app
docker compose stop app
```

### Bare-Metal `systemd` Path
```bash
sudo systemctl status opengenerate.service
sudo systemctl restart opengenerate.service
sudo journalctl -u opengenerate.service -f
```

---

## 2. Health Probes & Prometheus Metrics

OpenGenerate exposes an internal operational HTTP server on `127.0.0.1:8080` (`OG_OPS_HOST` / `OG_OPS_PORT`):

| Endpoint | Purpose | Healthy HTTP Code | Degraded / Unhealthy Code |
|---|---|---|---|
| `GET /livez` | Process liveness & event-loop responsiveness | `200 OK` | `503 Service Unavailable` |
| `GET /readyz` | PostgreSQL connectivity, Alembic revision match (`0001_baseline`), and subsystem readiness | `200 OK` | `503 Service Unavailable` |
| `GET /metrics` | Prometheus text-format metrics (queue depth, outbox backlog, DB latency, AI circuit states) | `200 OK` | — |

```bash
curl -fsS http://127.0.0.1:8080/livez
curl -fsS http://127.0.0.1:8080/readyz
curl -fsS http://127.0.0.1:8080/metrics
```

---

## 3. Backup & Restore Schedule

### Automated Verified Online Backup (`pg_dump -Fc`)
Run daily (and always before any schema upgrade or cutover):
```bash
pg_dump --format=custom --no-owner --dbname="${OG_DATABASE_URL}" --file="backups/og_$(date -u +%Y%m%dT%H%M%SZ).dump"
pg_restore --list "backups/og_latest.dump" >/dev/null
```

### Restore Verification into a Clean Instance
```bash
./deploy/rollback.sh restore-pg backups/og_latest.dump
```

---

## 4. Incident Diagnosis & Recovery Procedures

### 4.1 Resolving `delivery_unknown` Publications (`pub_journal`)
When a network timeout or process crash occurs after the Telegram HTTP request was sent but before the response was recorded, OpenGenerate transitions the journal entry to `delivery_unknown` and **never** auto-resends (preventing duplicate channel posts).
1. Open the Superadmin Console in Telegram (`🛡️ پنل سوپرادمین` → `🩺 سلامت سیستم` / `s:health`).
2. Check the target channel in Telegram to see whether the post actually appeared.
3. If the post appeared in the channel, mark the journal settled with its `telegram_msg_id`; if it did not appear, mark it `failed_safe` so the reserved daily post quota is released and the article returns to `ready`.

### 4.2 Resolving Stuck `processing` Payments (`pay_requests`)
Payment approval acquires a 5-minute lease (`status = 'processing'`, `lease_until = now() + 5 min`).
- If a worker crashes mid-approval, the lease expires automatically after 5 minutes and a second superadmin click on `✅ تایید` (`s:pay_ok:<id>`) safely resumes approval without double-crediting the wallet or referral reward (enforced by `idempotency_key` on `ledger_entries` and `plan_changes`).

### 4.3 Rotating Encryption Keys (`OG_ENCRYPTION_KEYS`)
`FieldCipher` uses a comma-separated keyring (`v1:<key_id>:<fernet_token>`):
1. Generate a new Fernet key:
   ```bash
   python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
2. Prepend the new key to `OG_ENCRYPTION_KEYS` while keeping the old key after a comma:
   `OG_ENCRYPTION_KEYS="<new_key>,<old_key>"`
3. Restart `app`. All new secrets encrypt with `<new_key>`, while existing rows encrypted with `<old_key>` continue to decrypt seamlessly.

### 4.4 Emergency Rollback
```bash
# Stop the new container stack immediately (legacy SQLite remains 100% untouched)
./deploy/rollback.sh stop-new
```
