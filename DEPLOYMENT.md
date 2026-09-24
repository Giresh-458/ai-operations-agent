# Production Deployment & Security Guide

This document outlines the security architecture and step-by-step production deployment instructions for the AI Operations Automation Platform.

---

## 1. Security Architecture & Threat Model

| Threat / Risk Vector | Mitigation Implemented |
|---|---|
| **Baking Secrets into Docker Images** | `.dockerignore` excludes `.env`, `.git`, `.venv`, and temporary files from image layers. |
| **Public Database Exposure** | `docker-compose.prod.yml` removes public port exposure (`5432`) and restricts PostgreSQL access strictly to the internal Docker network. |
| **SQL Injection** | All database queries are executed via SQLAlchemy ORM with strictly parameterized queries. Raw string concatenation is prohibited. |
| **Prompt Injection Attacks** | `guardrail_check` scans incoming requests for jailbreaks (*"ignore instructions"*, *"drop table"*) and halts execution before LLM or tool calling. |
| **PII Data Leakage** | Pattern-matching filters intercept sensitive financial and personal numbers (Credit Cards, SSNs) at the entry node. |
| **Cross-Site Request Forgery (CSRF)** | Streamlit XSRF protection enabled (`--server.enableXsrfProtection true`) alongside FastAPI `CORSMiddleware`. |
| **Credential Persistence** | User-entered API keys (Groq/OpenAI) are held in ephemeral session memory and are **never stored** in the database. |

---

## 2. Production Docker Deployment (VPS / Cloud Instance)

For deploying on Ubuntu/Debian Linux (AWS EC2, DigitalOcean, Hetzner, GCP Compute Engine):

### Step 1: Install Docker & Docker Compose
```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-plugin
sudo systemctl enable --now docker
```

### Step 2: Clone Repository & Create Production `.env`
```bash
git clone <your-repo-url> ai-operations-agent
cd ai-operations-agent
```

Generate a secure random password for PostgreSQL:
```bash
POSTGRES_PWD=$(openssl rand -hex 24)
cat <<EOF > .env
POSTGRES_USER=postgres
POSTGRES_PASSWORD=${POSTGRES_PWD}
POSTGRES_DB=operations_agent
OPENAI_MODEL=gpt-4o-mini
GROQ_MODEL=openai/gpt-oss-120b
LANGCHAIN_TRACING_V2=false
EOF
```

### Step 3: Launch with Hardened Production Stack
```bash
docker compose -f docker-compose.prod.yml up -d --build
```

---

## 3. Reverse Proxy & SSL Setup (HTTPS via Caddy)

Never expose HTTP ports `8080` or `8501` directly over the public internet. Use **Caddy** (which automatically provisions free Let's Encrypt SSL certificates) or **Nginx**.

### Example Caddyfile (`/etc/caddy/Caddyfile`)
```caddy
# Replace with your actual domain
agent.yourdomain.com {
    # Streamlit Web UI
    reverse_proxy 127.0.0.1:8501 {
        header_up Host {host}
        header_up X-Real-IP {remote}
    }
}

api.yourdomain.com {
    # FastAPI Backend
    reverse_proxy 127.0.0.1:8080
}
```

Reload Caddy:
```bash
sudo systemctl restart caddy
```
Caddy will automatically obtain and renew TLS certificates. All traffic is now encrypted via HTTPS.

---

## 4. Production Checklist Before Going Live

- [x] **`.dockerignore` active**: Verified that `.env` is never added to Docker build context.
- [x] **PostgreSQL Port 5432 closed to WAN**: Verified only accessible within Docker network.
- [x] **Strong DB credentials**: Verified unique random password configured in `.env`.
- [x] **Reverse Proxy configured**: SSL/TLS termination active via Caddy or Nginx.
- [x] **Guardrails verified**: Automated evaluation tests passing 100% on safety checks.
- [x] **Human-in-the-Loop active**: Critical actions require manual operator approval.
