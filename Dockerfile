FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN chmod +x start.sh
ENV PJ_REALTIME_BIND_HOST=0.0.0.0
EXPOSE 3001
CMD ["./start.sh"]
