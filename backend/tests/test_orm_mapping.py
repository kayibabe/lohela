"""Ensure all declarative relationships configure before runtime traffic."""

from sqlalchemy.orm import configure_mappers


def test_all_orm_mappers_configure():
    import app.models  # noqa: F401

    configure_mappers()
