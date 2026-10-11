"""Logging del borde: stderr (journald bajo systemd) y, opcionalmente, archivo rotativo."""
import logging
import logging.handlers
import sys

_FMT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging(s) -> logging.Logger:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(getattr(logging, s.log_level, logging.INFO))
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if s.log_to_file:
        log_dir = s.data_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(log_dir / "epp-edge.log", maxBytes=5_000_000, backupCount=3)
        )
    for h in handlers:
        h.setFormatter(logging.Formatter(_FMT))
        root.addHandler(h)
    for noisy in ("urllib3", "google", "websockets", "grpc"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return logging.getLogger("epp-edge")
