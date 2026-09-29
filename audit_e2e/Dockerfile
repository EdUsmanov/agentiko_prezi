# Cross-architecture manifest digest for Python 3.12 on Debian Bookworm.
FROM python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=UTC \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# A dated Debian snapshot makes system packages and Playwright's OS dependencies
# resolve to the same repository state on local machines and in CI.
RUN rm -f /etc/apt/sources.list /etc/apt/sources.list.d/* \
    && printf '%s\n' \
      'deb [check-valid-until=no] https://snapshot.debian.org/archive/debian/20260925T000000Z bookworm main' \
      'deb [check-valid-until=no] https://snapshot.debian.org/archive/debian-security/20260925T000000Z bookworm-security main' \
      > /etc/apt/sources.list.d/snapshot.list \
    && printf '%s\n' 'Acquire::Check-Valid-Until "false";' \
      > /etc/apt/apt.conf.d/99snapshot \
    && apt-get -o Acquire::Check-Valid-Until=false update \
    && apt-get install -y --no-install-recommends \
      git \
      nodejs \
      libreoffice-impress \
      fontconfig \
      fonts-dejavu-core \
      fonts-liberation \
      fonts-noto-core \
      fonts-noto-cjk \
      fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.lock requirements-e2e.txt ./
RUN python -m pip install --disable-pip-version-check --no-cache-dir \
      -r requirements.lock -r requirements-e2e.txt \
    && python -m playwright install-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && python -m playwright install chromium

COPY . .
RUN mkdir -p /app/audit_e2e/results

CMD ["python", "-m", "pytest", "-q", "audit_e2e/tests/test_eval_runtime.py", "audit_e2e/e2e/test_evaluation_workflows.py"]
