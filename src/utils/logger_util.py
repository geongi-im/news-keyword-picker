import logging
from datetime import datetime
from pathlib import Path


class LoggerUtil:
    _logger = None

    def __init__(self, name: str = "news_keyword_picker", log_dir=None):
        """Create the shared application logger."""
        if LoggerUtil._logger is None:
            LoggerUtil._logger = self._create_logger(name, log_dir)

    def _create_logger(self, name: str, log_dir=None) -> logging.Logger:
        """Create a logger that writes to console and project-root logs."""
        logger = logging.getLogger(name)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

        if logger.handlers:
            return logger

        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )

        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(logging.INFO)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

        resolved_log_dir = Path(log_dir) if log_dir else self._default_log_dir()
        resolved_log_dir.mkdir(parents=True, exist_ok=True)
        log_file = resolved_log_dir / f"{name}_{datetime.now():%Y%m%d}.log"

        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        return logger

    def _default_log_dir(self) -> Path:
        """Return the project-root logs directory."""
        return Path(__file__).resolve().parents[2] / "logs"

    def get_logger(self) -> logging.Logger:
        """Return the shared application logger."""
        return LoggerUtil._logger
