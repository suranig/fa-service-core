# FA Service Core

[![PyPI version](https://img.shields.io/pypi/v/fa-service-core.svg)](https://pypi.org/project/fa-service-core/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/fa-service-core.svg)](https://pypi.org/project/fa-service-core/)

FastAPI microservices core library dla systemu CMS z multi-tenancy i Row-Level Security.

## 🚀 Główne funkcjonalności

- **🏗️ Repository Pattern** - generyczna klasa bazowa z filtrami, sortowaniem, paginacją
- **🔍 Query Parameters** - automatyczny parsing parametrów FastAPI z Pydantic v2
- **🗄️ Dwu-poolowe połączenia** - oddzielne poole write/read z PgBouncer
- **🔒 Row Level Security (RLS)** - izolacja danych na poziomie site/tenant
- **⚖️ Unit of Work pattern** - zarządzanie transakcjami z kontekstem site
- **🔄 Idempotency middleware** - deduplikacja requestów
- **📝 Audit logging** - śledzenie zmian z JSON Patch RFC6902
- **📦 Wersjonowanie** - historia zmian zasobów
- **📤 Outbox pattern** - niezawodne publikowanie eventów
- **👁️ Observability** - request tracing, strukturalne logowanie
- **📋 Schema-driven API** - automatyczne generowanie schematów dla frontend
- **⚡ Custom actions** - rozszerzalne akcje biznesowe

## Wymagania

- Python 3.13+
- PostgreSQL 17.6+
- Redis (opcjonalnie, dla cache'u)

## 📦 Instalacja

```bash
# Instalacja z PyPI
pip install fa-service-core

# Lub instalacja z źródeł
git clone <repository-url>
cd fa-service-core
pip install -e .
```

## 🚀 Quick Start

### 1. Stwórz mikroservice z Repository pattern

```python
# models.py
from uuid import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from core.models import Base

class Page(Base):
    __tablename__ = "pages"
    
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    site_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="draft")

# repository.py
from core.repository import BaseRepository
from core.uow import write_uow, read_uow
from sqlalchemy.ext.asyncio import AsyncSession

class PageRepository(BaseRepository[Page]):
    def __init__(self):
        super().__init__(Page)
    
    async def get_by_id(self, session: AsyncSession, site_id: UUID, entity_id: UUID) -> Page | None:
        result = await session.get(Page, entity_id)
        return result if result and result.site_id == site_id else None
    
    async def create(self, session: AsyncSession, site_id: UUID, data: dict) -> Page:
        page = Page(site_id=site_id, **data)
        session.add(page)
        await session.flush()
        return page

# FastAPI endpoint
from fastapi import FastAPI, Depends
from core.site_resolver import site_id_dep
from core.query_params import QueryParams

app = FastAPI()
page_repo = PageRepository()

@app.get("/pages")
async def list_pages(
    query: QueryParams = Depends(),
    site_id: UUID = Depends(site_id_dep),
):
    options = query.to_query_options()
    async with read_uow(site_id) as session:
        result = await page_repo.list(session, site_id, options)
    return result
```

### 2. Użyj filtrów i sortowania

```bash
# Podstawowe listowanie
GET /pages?page=1&page_size=20

# Filtrowanie
GET /pages?filter[]=status:eq:published&filter[]=created_at:gte:2024-01-01

# Sortowanie
GET /pages?sort=created_at:desc,title:asc

# Wyszukiwanie
GET /pages?search=lorem&filter[]=status:in:published,draft

# Kombinacja wszystkich
GET /pages?filter[]=status:eq:published&sort=created_at:desc&page=2&page_size=10&search=fastapi
```

### 4. Dostępne operatory filtrów

```bash
# Operatory porównania
filter[]=price:eq:100          # równe
filter[]=price:ne:100          # nie równe  
filter[]=price:gt:100          # większe niż
filter[]=price:gte:100         # większe lub równe
filter[]=price:lt:100          # mniejsze niż
filter[]=price:lte:100         # mniejsze lub równe

# Operatory tekstowe
filter[]=title:like:%lorem%    # zawiera (LIKE)
filter[]=title:ilike:%Lorem%   # zawiera bez case-sensitive

# Operatory list
filter[]=status:in:draft,published,archived    # w liście
filter[]=status:not_in:spam,deleted           # nie w liście

# Operatory NULL
filter[]=deleted_at:is_null        # jest NULL
filter[]=deleted_at:is_not_null    # nie jest NULL

# Operatory zakresu
filter[]=created_at:between:2024-01-01,2024-12-31

# PostgreSQL JSONB (dla pól JSON)
filter[]=metadata:contains:{"featured":true}
filter[]=tags:jsonb_path:$.tags[*] ? (@ == "fastapi")
```

### 5. Użyj Custom Actions

```python
from core.actions import action, ActionContext, ActionResult

@action(name="publish", resource="pages")
async def publish_page(context: ActionContext, payload: dict) -> ActionResult:
    # Implementacja publikacji strony
    async with write_uow(context.site_id) as session:
        page = await page_repo.get_by_id(session, context.site_id, context.resource_id)
        if not page:
            return ActionResult(success=False, message="Page not found")
        
        page.status = "published"
        await session.flush()
        
        return ActionResult(
            success=True,
            message="Page published successfully",
            data={"status": page.status}
        )

# Użycie w endpoint
@app.post("/pages/{page_id}:publish")
async def publish_page_endpoint(
    page_id: UUID,
    site_id: UUID = Depends(site_id_dep)
):
    context = ActionContext(site_id=site_id, resource_id=page_id, action_name="publish")
    return await publish_page(context, {})
```

### 6. Ustaw bazy danych

```python
# main.py
from core.db import DatabaseManager, get_db_manager
from core.site_resolver import init_site_resolver

async def startup():
    # Inicializuj połączenia do bazy
    db_manager = get_db_manager()
    await db_manager.init_db(
        write_db_url="postgresql+asyncpg://user:pass@localhost/db",
        read_db_url="postgresql+asyncpg://user:pass@localhost/db_read"
    )
    
    # Inicializuj site resolver
    await init_site_resolver(db_manager, cache_ttl=300)

app.add_event_handler("startup", startup)
```

## Konfiguracja środowiska

```bash
# Skopiuj przykładową konfigurację
cp env.example .env

# Edytuj konfigurację
vim .env
```

### Zmienne środowiskowe

```bash
# Bazy danych
DATABASE_WRITE_URL=postgresql+asyncpg://fa_user:fa_password@localhost:5432/fa_cms
DATABASE_READ_URL=postgresql+asyncpg://fa_user:fa_password@localhost:5433/fa_cms


# Aplikacja
APP_NAME=fa-service-core
LOG_LEVEL=INFO
SITE_CACHE_TTL=60
```

## Uruchomienie środowiska deweloperskiego

```bash
# Uruchom bazy danych
docker-compose up -d

# Zainicjalizuj bazę danych
make migrate-init

## Użycie

### Podstawowa konfiguracja

```python
import asyncio
from core.db import init_database
from core.site_resolver import init_site_resolver
from core.uow import init_uow_manager

async def setup():
    # Inicjalizacja komponentów core
    init_database(
        write_url="postgresql+asyncpg://...",
        read_url="postgresql+asyncpg://...",
    )
    init_site_resolver(cache_ttl=60)
    init_uow_manager()
```

### Unit of Work z kontekstem site

```python
from core.uow import write_uow, read_uow
from uuid import UUID

# Operacje zapisu
async with write_uow(site_id: UUID) as session:
    # SET LOCAL app.current_site = site_id
    # Wszystkie operacje są izolowane do tego site
    page = Page(site_id=site_id, title="Test")
    session.add(page)
    # Commit automatyczny

# Operacje odczytu
async with read_uow(site_id: UUID) as session:
    # READ ONLY transaction
    # SET LOCAL app.current_site = site_id
    pages = await session.execute(select(Page))
```

### Audit logging

```python
from core.audit import AuditManager

# Rejestrowanie zmian
await AuditManager.record_update(
    session=session,
    site_id=site_id,
    user_id=user_id,
    resource="pages",
    resource_id=page_id,
    version=2,
    before={"title": "Old Title"},
    after={"title": "New Title"},
)

# Historia zmian
history = await list_history(session, "pages", page_id)
```

### Outbox pattern

```python
from core.outbox import enqueue_domain_event

# Wysyłanie event'u
await enqueue_domain_event(
    session=session,
    site_id=site_id,
    aggregate="pages",
    aggregate_id=page_id,
    event_name="published",
    data={"title": "Page Title"},
    version=1,
)
```

### Custom actions

```python
from core.actions import action, ActionContext, ActionResult

@action(name="publish", resource="pages")
async def publish_page(
    session: AsyncSession,
    context: ActionContext,
    payload: dict,
) -> ActionResult:
    # Logika publikacji
    # Automatyczne: audit, outbox, idempotency
    return ActionResult(
        success=True,
        message="Page published",
        version=2,
    )
```

### Schema API

```python
from core.schema_api import resource_schema, create_pages_schema

@resource_schema(
    schema_dict=create_pages_schema(),
    ui_config={
        "list": {"columns": ["title", "status", "updated_at"]},
        "form": {"layout": [["title"], ["slug", "status"]]},
    },
    actions=[
        {"name": "publish", "label": "Publish", "icon": "send"},
        {"name": "archive", "label": "Archive", "icon": "archive"},
    ],
)
def setup_pages_schema():
    pass
```

### FastAPI integration

```python
from fastapi import FastAPI, Depends
from core.site_resolver import site_dep
from core.idempotency import IdempotencyMiddleware
from core.observability import RequestTrackingMiddleware

app = FastAPI()

# Middleware
app.add_middleware(RequestTrackingMiddleware)
app.add_middleware(IdempotencyMiddleware)

@app.get("/pages/{page_id}")
async def get_page(
    page_id: UUID,
    site: Site = Depends(site_dep),
):
    async with read_uow(site.id) as session:
        # Automatyczny RLS filtering
        page = await session.get(Page, page_id)
        return page
```

## Migracje

```bash
# Tworzenie nowej migracji
make migrate-create MESSAGE="Add new table"

# Uruchomienie migracji
make migrate-upgrade

# Cofnięcie migracji
make migrate-downgrade

# Status migracji
make migrate-current

# Historia migracji
make migrate-history

# Reset bazy (development)
make migrate-reset
```

## Testy

```bash
# Uruchomienie testów
make test

# Testy z coverage
make test-cov

# Linting
make lint

# Formatowanie kodu
make format
```

## Architektura

### Row Level Security (RLS)

Wszystkie tabele używają RLS do izolacji danych:

```sql
-- Automatycznie stosowane w transakcjach
SET LOCAL app.current_site = 'site-uuid';

-- Polityki RLS
CREATE POLICY pages_policy ON pages
    FOR ALL
    USING (site_id::text = current_setting('app.current_site', true));
```

### Outbox Pattern

```
[Write Operation] -> [Outbox Event] -> [Projector] -> [Read Model]
                                   -> [External Event]
```

### Pools baz danych

- **Write Pool**: Primary database, transakcje write
- **Read Pool**: Replica database, tylko odczyt
- **PgBouncer**: Transaction pooling, wyłączony prepared statement cache

### Monitoring

- **Request ID**: X-Request-ID w nagłówkach
- **Health checks**: `/healthz`, `/readyz`
- **Structured logging**: JSON logs z kontekstem
- **Basic endpoint**: `/metrics` (informacyjny)

## Struktura projektu

```
fa-service-core/
├── core/                   # Core library
│   ├── models.py          # SQLAlchemy models
│   ├── db.py              # Database management
│   ├── uow.py             # Unit of Work
│   ├── site_resolver.py   # Site resolution
│   ├── audit.py           # Audit logging
│   ├── versions.py        # Resource versioning
│   ├── outbox.py          # Outbox pattern
│ │   ├── idempotency.py     # Idempotency middleware
│   ├── actions.py         # Custom actions
│   ├── schema_api.py      # Schema-driven API
│   ├── observability.py   # Metrics & monitoring
│   ├── errors.py          # Error handling
│   └── migrations.py      # Migration utilities
├── alembic/               # Database migrations
├── scripts/               # Setup scripts
├── tests/                 # Tests
├── examples/              # Usage examples
└── docker-compose.yml     # Development environment
```

## Przykłady użycia

Sprawdź katalog `examples/` dla pełnych przykładów:

- `basic_usage.py` - Podstawowe użycie core library
- `fastapi_integration.py` - Integracja z FastAPI
- `projector_worker.py` - Worker do przetwarzania outbox events

## Rozwój

```bash
# Setup środowiska deweloperskiego
make dev-setup

# Cykl deweloperski
make dev-cycle  # format + lint + test

# Uruchomienie z hot reload
uvicorn examples.fastapi_app:app --reload
```

## Licencja

MIT License
