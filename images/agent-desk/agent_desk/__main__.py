import sys

from . import config, server
from .dispatcher import Dispatcher
from .github import GitHub
from .jobs import Kube
from .plane import Plane


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    cfg = config.load()
    if not cfg.webhook_secret:
        log("WEBHOOK_SECRET_FILE is empty: every delivery will be rejected; polling only")
    plane = Plane(cfg.plane_url, cfg.workspace, cfg.plane_token, log=log)
    kube = Kube(cfg.job_namespace, cfg.console_deployment)
    gh = GitHub(cfg.github_token) if cfg.github_token else None
    server.run(Dispatcher(cfg, plane, kube, gh, log=log), cfg, log=log)
    return 0


if __name__ == "__main__":
    sys.exit(main())
