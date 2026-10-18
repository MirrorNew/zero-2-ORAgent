FROM python:3.12-slim
RUN pip install --no-cache-dir gurobipy==12.0.2 coptpy==8.0.5 numpy==2.2.5 scipy==1.15.3
COPY worker.py collect.py /
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/output TMPDIR=/output OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
USER 65534:65534
CMD ["sleep", "infinity"]
