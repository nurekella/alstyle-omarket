from sqlalchemy import (
    Column, Integer, String, Float, Boolean, Text, DateTime, func, Index,
)
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True)
    name = Column(String(500), nullable=False)
    parent_id = Column(Integer, nullable=True)
    level = Column(Integer, default=1)
    left_key = Column(Integer, default=0)
    right_key = Column(Integer, default=0)
    elements_count = Column(Integer, default=0)
    sync_enabled = Column(Boolean, default=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class Product(Base):
    __tablename__ = "products"

    article = Column(Integer, primary_key=True)
    article_pn = Column(String(200), nullable=True)
    name = Column(String(500), nullable=False)
    full_name = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    category_id = Column(Integer, nullable=True)
    brand = Column(String(200), nullable=True)

    price_dealer = Column(Float, nullable=True)
    price_retail = Column(Float, nullable=True)
    price_omarket = Column(Float, nullable=True)

    quantity = Column(String(50), default="0")
    is_new = Column(Boolean, default=False)
    barcode = Column(String(100), nullable=True)
    warranty = Column(String(200), nullable=True)
    weight = Column(String(50), nullable=True)
    images = Column(Text, nullable=True)

    quantity_markdown = Column(Integer, default=0)
    price_markdown = Column(Float, nullable=True)

    is_active = Column(Boolean, default=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_products_category", "category_id"),
        Index("ix_products_active", "is_active"),
    )


class SyncLog(Base):
    __tablename__ = "sync_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    started_at = Column(DateTime, server_default=func.now())
    finished_at = Column(DateTime, nullable=True)
    status = Column(String(20), default="running")
    products_fetched = Column(Integer, default=0)
    products_updated = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)


class Setting(Base):
    __tablename__ = "settings"

    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=False)


# --- Engine & session (async SQLite) ---

settings = get_settings()
engine = create_async_engine(f"sqlite+aiosqlite:///{settings.db_path}", echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
