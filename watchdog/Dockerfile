# Copyright 2026 Danny Sauer
# SPDX-License-Identifier: Apache-2.0

FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY frigate_watchdog.py .

CMD ["python", "frigate_watchdog.py"]
