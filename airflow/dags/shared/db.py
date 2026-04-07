from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from shared.config import DB_USER, DB_PASSWORD, DB_HOST, DB_NAME


def make_mariadb_engine() -> Engine:
    """Return a SQLAlchemy engine for the MariaDB instance.

    Why mysql+pymysql? SQLAlchemy needs a driver prefix; pymysql is a
    pure-Python MySQL/MariaDB driver that requires no C extensions to install.
    DB_HOST points to MariaDB's private EC2 IP — reachable from inside the K8s
    cluster because MariaDB runs on the same EC2 host (outside the pods).
    """
    return create_engine(f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}")
