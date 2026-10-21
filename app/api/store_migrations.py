"""旧 store 数据库迁移 REST API。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config.settings import load_settings
from app.deps import config_store
from app.services import pg_databases
from app.services import store_migrations as migration_service

router = APIRouter(prefix="/api/store-migrations", tags=["store-migrations"])


class NamespaceMapping(BaseModel):
    source: str = ""
    target: str = ""


class SourceSelection(BaseModel):
    id: str
    namespaces: list[NamespaceMapping] = Field(default_factory=list)


class RunMigrationsBody(BaseModel):
    sources: list[SourceSelection] = Field(default_factory=list)


@router.get("")
async def get_store_migrations(probe: bool = True):
    settings = load_settings(config_store._dir)
    return await migration_service.list_sources(
        config_store._dir, settings, probe=probe
    )


@router.post("/run")
async def run_store_migrations(body: RunMigrationsBody):
    settings = load_settings(config_store._dir)
    try:
        return await migration_service.run_migrations(
            config_store,
            settings,
            [s.model_dump() for s in body.sources],
        )
    except pg_databases.DatabaseError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/prompted")
def mark_store_migration_prompted():
    migration_service.mark_prompted(config_store._dir)
    return {"ok": True}


@router.delete("/{source_id}")
def remove_store_migration_source(source_id: str):
    migration_service.remove_source(config_store._dir, source_id)
    return {"ok": True}
