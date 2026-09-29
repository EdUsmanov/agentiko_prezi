"""Install only the explicit evaluation proxy in isolated Python subprocesses."""

import os

if os.environ.get("STUDIO_TEST_LIVE_PROXY_URL"):
    from audit_e2e.live_provider import install_worker_transport_from_env

    install_worker_transport_from_env()
