"""Loggers de auditoría del agente: una línea JSON a stderr, también cuando nadie ha configurado ``logging``.

Con uvicorn o Celery el logger raíz queda en WARNING: sin esto, los registros INFO se perdían en el servidor aunque las
pruebas (que fuerzan el nivel) los vieran.
"""

from __future__ import annotations

import logging
import sys


def json_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    return logger
