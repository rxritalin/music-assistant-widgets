FROM python:3.12-alpine

RUN apk add --no-cache git

# Clone the monorepo so all widget directories (MA-Roon, etc.) and server/app.py
# are available even when Home Assistant Supervisor builds from the ma-widgets-addon/ subfolder.
RUN git clone --depth 1 https://github.com/rxritalin/music-assistant-widgets.git /app \
    && rm -rf /app/.git /app/*.icuewidget /app/assets

ENV MA_WIDGETS_ROOT=/app
ENV MA_WIDGETS_DATA=/data
ENV PORT=8096

EXPOSE 8096

CMD ["python3", "/app/server/app.py"]
