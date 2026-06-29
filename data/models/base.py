"""
data/models/base.py — Shared declarative base
===============================================
All ORM models inherit from this Base.
Kept in its own file to avoid circular imports.
"""

from sqlalchemy.orm import declarative_base

Base = declarative_base()
