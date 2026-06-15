"""Merkezi logger yapılandırması."""
import logging
import sys


def get_logger(name: str) -> logging.Logger:
    """Modül bazlı logger döndürür.

    Tekrar tekrar çağrıldığında handler çoğaltmaz. Çıktıyı stdout'a yazar.
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.propagate = False
    return logger
