# AI Engineering Company Project — Student Template

[![4Geeks Academy](https://img.shields.io/badge/4Geeks-Academy-blue)](https://4geeksacademy.com)
[![AI Engineering](https://img.shields.io/badge/track-AI%20Engineering-green)](https://4geeksacademy.com/es/programas-de-carrera/ingenieria-ia)

_Base template for transversal projects in the AI Engineering Career Program — 4Geeks Academy._

> _Instrucciones disponibles en español en [README.es.md](./README.es.md)._

---

## Purpose

This repository is the **starter template** for transversal projects. You will work on real company scenarios (Brasaland, TrackFlow, Nexova), building deliverables that map to course milestones (Web, Programming, Backend, Telemetry, RAG, Agents, Workflows, Real-time).

- Create a template from this repository.
- Replace the placeholder `CONTEXT.md` with your assigned company context.
- Use `skills/` and the directory-level `README.md` files as working guidance.

---

## Current status of the template

The repository currently provides a **base folder structure and documentation skeleton**. It does not include runnable apps or global scripts yet.

- `CONTEXT.md` is a placeholder and must be replaced with your assigned company context.
- There is no root `AGENTS.md` yet.
- Shared package metadata exists in `packages/shared/package.json` (`@repo/shared-types`), but no workspace runner is configured at root.

---

## Repository structure

```text
ai-engineering-company-project-monorepo/
├── README.md
├── README.es.md
├── CONTEXT.md                # Placeholder to be replaced with assigned context
├── agents/                   # Agent patterns/templates and tools docs
├── data/                     # raw, process, pipelines, eval
├── docs/                     # Project and architecture documentation
├── infra/                    # Docker, Terraform, deployment configs
├── internal/                 # CLIs, packaged migration scripts, internal utilities
├── mcps/                     # Model Context Protocol (MCP) Servers
├── packages/
│   └── shared/               # Shared package (@repo/shared-types)
├── scripts/                  # Script conventions/documentation
├── services/                 # APIs and background workers
├── shared/                   # Shared assets/conventions at repo level
├── skills/                   # Reusable agent skills
├── uis/                      # User interfaces (React, Next.js, Streamlit, HTML)
└── workflows/                # Automation/orchestration documentation
```

---

## How to start

1. **Use this repository as a template** and create your own project repo.
2. **Clone** your repository (or open it in Codespaces).
3. **Replace** `CONTEXT.md` with the full context for your assigned company.
4. **Review** each top-level folder `README.md` to understand intended responsibilities (`uis/`, `services/`, `data/`, `skills/`, etc.).
5. **Start implementing** milestone deliverables in `uis/` and `services/`, reusing `packages/shared/` and `data/` as needed.

---

## Docker Development (TrackFlow)

Requires Docker Engine and Docker Compose 2.17+ (BuildKit additional contexts).
The existing interfaces are React/Vite, not Next.js. Both run in one Node Alpine
container through `uis/start.sh`, on ports 3000 and 3001. The Python container
keeps the existing Node CSV gateway (8080) and starts Uvicorn with `--reload`
(8081). Python dependencies are installed with `uv pip install --system -r
requirements.txt`; the requirements are exported from the existing `uv.lock`.

Obtain the root `.env` through the team's secure channel before the first run.
It is ignored by Git; do not commit it or share `docker compose config` output,
which contains resolved secrets. No Node or Python installation is needed on
the host. From the repository root, install/build and start with:

```bash
docker compose up --build
```

Subsequent starts: `docker compose up`. For background mode and health checks:
`docker compose up -d --wait`. Open `http://localhost:3000` (website),
`http://localhost:3001` (backoffice), and `http://localhost:8081/docs` (API).
Both UI servers bind to `0.0.0.0` inside Docker, so Codespaces port forwarding
works. Host publication defaults to loopback through `BIND_ADDRESS` in `.env`.

All application settings come from the root `.env`. Backend credentials include
`SECRET_KEY`, `DATABASE_URL`, and optional `RESEND_API_KEY`/`RESEND_FROM_EMAIL`.
The browser uses relative API paths. Container proxies use
`API_PROXY_TARGET=http://backend:8080`,
`AUTH_PROXY_TARGET=http://backend:8081`, and
`NEXT_PUBLIC_INVENTORY_API_URL=http://backend:8081`, never host loopback.
`SUPPLIERS_HOST=backend` also routes the internal gateway through Docker DNS.
`DOCKER_NETWORK_NAME` explicitly names the shared bridge network.

Source bind mounts enable Vite hot reload and Uvicorn reload; dependency and
TinyDB volumes persist independently. `DATABASE_URL` retains the configured
PostgreSQL database, which must be reachable from Docker. Stop without deleting
data with `docker compose down`; do not use `down -v` unless data loss is intended.
After changing frontend dependencies, run `docker compose exec interfaces npm
--prefix /app/uis/website ci` (or `/app/uis/backoffice`) to refresh its volume.
Regenerate Python requirements with `uv export --project services/api --no-dev
--no-emit-project --no-hashes --no-header --format requirements-txt
--output-file services/requirements.txt`, then rebuild the backend.

Validation on 2026-11-05: both images build, Compose validates, and both UI pages
return HTTP 200. Full startup is blocked by DNS resolution of the configured
Supabase database inside Docker; interface-to-backend TCP also times out despite
correct Docker service hostnames. VPS deployment has not been performed: no VPS
address, SSH configuration, or authentication agent is available. Production
needs its own private `.env`, confirmed SSH access, TLS, and production servers
instead of Vite/Uvicorn reload. This development stack is not a production sign-off.

## Run the landing locally (Codespaces)

For this web milestone (HTML + Tailwind CDN + JS), run a static server with:

```bash
npx serve . -l 4173
```

Then open:

- `http://127.0.0.1:4173/index.html`
- `http://127.0.0.1:4173/application.html`

This command is Codespaces-compatible and needs no extra setup.

## TypeScript validation for milestone 2

Install dependencies:

```bash
npm install
```

Run strict typecheck for the data utilities:

```bash
npm run typecheck
```

---

## Milestones (reference)

| Milestone | Focus        | Typical deliverables                        |
| --------- | ------------ | ------------------------------------------- |
| 0         | Prework      | Environment setup, first prompts            |
| 1         | Web          | Corporate website, forms, SEO               |
| 2         | Programming  | Business logic, scoring, calculations       |
| 3         | AI-driven UI | AI-generated interfaces                     |
| 4         | Next.js      | Portals, loyalty app, operations UI         |
| 5         | Backend      | Central API (locations, menus, sales, etc.) |
| 6         | Telemetry    | Data pipeline, dashboards                   |
| 7         | RAG & Memory | Semantic knowledge base, search             |
| 8         | Agents       | Support, onboarding, training agents        |
| 9         | Workflows    | n8n automations                             |
| 10        | Real-time    | Live dashboards, alerts, streaming          |

---

## Links

- [4Geeks Academy — AI Engineering](https://4geeksacademy.com/es/programas-de-carrera/ingenieria-ia)
- [How to start a coding project](https://4geeks.com/lesson/how-to-start-a-project)

---

## Contributors

This template was built as part of the 4Geeks Academy AI Engineering Career Program by [@marcogonzalo](https://www.linkedin.com/in/marcogonzalo) and [@alezanchezr](https://x.com/alesanchezr) and many other contributors. Find out more about our [AI Engineering Course](https://4geeksacademy.com/en/career-programs/ai-engineering), and [other courses](https://4geeksacademy.com/en/program-comparison).

You can find other templates and resources like this at the [4Geeks Academy GitHub page](https://github.com/4geeksacademy).

_This template is maintained by 4Geeks Academy for the AI Engineering track. For exclusive use in the programme._
