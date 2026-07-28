"""Consistent logging setup for operation modules."""
import logging


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"pj.ops.{name}")
