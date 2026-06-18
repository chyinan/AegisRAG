# Enterprise Login & User Group Management — Implementation Plan

> **For Hermes:** Implement task-by-task, commit after each.

**Goal:** Replace demo persona "click-to-enter" with real login (username+password), JWT sessions, and user group management.

**Architecture:** 
- Backend: `packages/auth/models.py` with bcrypt, `POST /auth/login` returning JWT, `/groups` and `/users` CRUD. Keep dev_headers fallback.
- Frontend: Login form (username/password) in AuthGate, demo personas remain for dev.

**Tech Stack:** FastAPI, bcrypt, PyJWT, SQLAlchemy, Next.js/React.

---

## Phase 1: Backend — User Model & Auth

### Task 1: Add bcrypt to pyproject.toml, `uv sync`

### Task 2: Create `packages/auth/models.py` — User + UserGroup models
- User: id, username, password_hash, display_name, email, roles, permissions, department, tenant_id, is_active
- UserGroup: id, name, description, roles, permissions, tenant_id
- user_group_membership association table (M2M)
- set_password / check_password using bcrypt
- roles_list / permissions_list properties (comma-separated)

### Task 3: Alembic migration for users + user_groups tables
- `uv run alembic revision --autogenerate -m "add users and user_groups"`
- `uv run alembic upgrade head`

### Task 4: Create `apps/api/routes/auth.py` — POST /auth/login
- Accepts {username, password}
- Validates against User model, returns JWT with sub, username, tenant_id, roles, permissions
- Register in apps/api/main.py

### Task 5: Create `packages/auth/seed.py` — seed 5 default users + 3 groups
- admin/admin123, employee/demo123, knowledge_manager/demo123, ai_engineer/demo123, auditor/demo123
- Groups: HR Team, Platform Team, Risk & Audit

---

## Phase 2: Frontend — Login Form

### Task 6: Add login form to AuthGate (above persona grid)
- Username + password inputs + Sign In button
- Calls POST /api/auth/login, receives JWT, creates bearer AuthSession
- Error handling for invalid credentials

### Task 7: Add i18n strings (enterpriseLogin, signIn, etc.)

### Task 8: Next.js proxy /api/auth/* → FastAPI backend in next.config.ts

---

## Phase 3: User & Group Management API

### Task 9: Create `apps/api/routes/groups.py` — CRUD /groups
- GET /groups — list all groups with members
- POST /groups — create group
- POST /groups/{id}/members — add user
- DELETE /groups/{id}/members/{user_id} — remove user

### Task 10: Create `apps/api/routes/users.py` — GET/POST /users
- GET /users — list all active users
- POST /users — create user with password

---

## Phase 4: Integration

### Task 11: Add seed service to docker/compose.yaml (runs after migration)

### Task 12: End-to-end test — login via frontend, verify groups API

## Default Credentials

| Username | Password | Role |
|----------|----------|------|
| admin | admin123 | Platform Admin |
| employee | demo123 | Employee |
| knowledge_manager | demo123 | Knowledge Manager |
| ai_engineer | demo123 | AI Engineer |
| auditor | demo123 | Auditor |
