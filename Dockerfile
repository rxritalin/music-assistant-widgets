FROM python:3.12-alpine

WORKDIR /app

COPY server/ /app/server/
COPY MA-Roon/ /app/MA-Roon/

ENV MA_WIDGETS_ROOT=/app
ENV MA_WIDGETS_DATA=/data
ENV PORT=8096

VOLUME ["/data"]
EXPOSE 8096

CMD ["python3", "/app/server/app.py"]
