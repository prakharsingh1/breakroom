FROM python:3.12.14-slim
WORKDIR /work
COPY packages/breakroom-core /work/packages/breakroom-core
COPY examples /work/examples
RUN python -m pip install --no-cache-dir ./packages/breakroom-core
ENV PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["breakroom"]
