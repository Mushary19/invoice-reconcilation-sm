FROM python:3.11-slim
WORKDIR /app
COPY generate_invoices.py mock_erp.py ./
RUN pip install --no-cache-dir fastapi uvicorn
EXPOSE 9100
CMD ["uvicorn", "mock_erp:app", "--host", "0.0.0.0", "--port", "9100"]
