# Deployment and Security Guide

This repository is a portfolio/demo application. The controls below reduce common risks; they do not make the system production-ready or provide per-user authorization.

## 1. Security and operational boundaries

- **API authentication:** Operational API routes require `Authorization: Bearer <API_ACCESS_TOKEN>`. Use a unique random token of at least 32 characters. It is a shared credential, not a user/role system.
- **Secrets:** Never commit `.env`, provider keys, database passwords, or production tokens. User-entered LLM keys are held in process-local memory while a task runs; this is not suitable for multi-worker deployments or a managed secrets service.
- **Database exposure:** The production Compose file does not publish PostgreSQL's port and keeps services on an internal Docker network.
- **Transport security:** The Compose ports bind to loopback. Put a reverse proxy with HTTPS in front of the UI/API before remote use.
- **Request safety:** Parameter validation and heuristic PII/prompt-injection checks are defense-in-depth, not complete detection or authorization. Do not submit real customer data.
- **Audit trail:** Database audit rows are useful for debugging, but are not immutable or compliance-grade.
- **Database setup:** The application creates tables and seeds demo records on startup. There is no migration framework yet; back up data before schema changes and do not treat automatic table creation as a production migration strategy.

## 2. Production Docker deployment (VPS / cloud VM)

### Prerequisites

Install Docker Engine and the Docker Compose plugin, then clone the repository:

```bash
git clone https://github.com/Giresh-458/ai-operations-agent.git
cd ai-operations-agent
```

### Configure secrets

Create a local `.env` file (it is excluded from the Docker build context and Git):

```bash
POSTGRES_USER=operations_agent
POSTGRES_PASSWORD=<a-long-random-database-password>
POSTGRES_DB=operations_agent
API_ACCESS_TOKEN=<a-random-secret-at-least-32-characters>
CORS_ORIGINS=https://your-ui-domain.example
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
GROQ_MODEL=openai/gpt-oss-120b
LANGCHAIN_TRACING_V2=false
LANGCHAIN_API_KEY=
LANGCHAIN_PROJECT=AIOpsAgent
```

Generate secrets instead of copying the placeholders. For example:

```bash
openssl rand -hex 32
```

Use one generated value for `POSTGRES_PASSWORD` and a separate generated value for `API_ACCESS_TOKEN`. Set `CORS_ORIGINS` to the exact browser origins that need access; do not use a wildcard. The Streamlit UI receives `API_ACCESS_TOKEN` as `BACKEND_API_TOKEN` through Compose. Provider API keys can be entered at runtime in the UI or supplied through the environment.

### Launch and verify

```bash
docker compose -f docker-compose.prod.yml config
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs --tail=100 web ui
```

The backend and UI define health checks. Confirm both report healthy before routing external traffic. The API liveness endpoint is `/health`; `/ready` checks database connectivity.

## 3. Reverse proxy and HTTPS

Do not expose ports 8080 or 8501 directly to the public internet. The Compose file binds both to loopback for a reverse proxy. Configure Caddy or Nginx to terminate HTTPS and proxy to `127.0.0.1:8501` for Streamlit. If exposing the API on a separate host, proxy to `127.0.0.1:8080` and set `CORS_ORIGINS` to the exact browser origin(s) that will call it.

## 4. Before any real-world deployment

- Implement per-user authentication and authorization; scope each task and approval to its owner/role.
- Move provider credentials to a proper secrets manager and replace process-local key storage.
- Add schema migrations, backups/restore drills, retention rules, and monitoring.
- Make mutation tools idempotent and define recovery behavior for ambiguous database/remote side effects.
- Expand adversarial security tests and RAG evaluation datasets; verify groundedness and refusal behavior against a reviewed test set.
- Review data retention and PII handling before using real customer information.
- Run dependency/security scans and load tests on the exact deployment image.

Do not interpret passing CI smoke tests as proof of production readiness.
