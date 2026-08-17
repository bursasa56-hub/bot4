FROM python:3.12

WORKDIR /app
ENV PYTHONUNBUFFERED=1
ENV PORT=80

COPY . /app
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 80
CMD ["python", "-u", "bot.py"]
