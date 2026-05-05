import sys
import logging
from logging.handlers import RotatingFileHandler
import os

sys.path.insert(0, "/var/www/serverjonas-hub")
from toolbox import LOGS_DIR,

os.makedirs(LOGS_DIR, exist_ok=True)

# Logger einrichten
handler = RotatingFileHandler(
    os.path.join(LOGS_DIR, 'wsgi.log'),
    maxBytes=10*1024*1024,  # 10MB
    backupCount=5
)
handler.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)

# Root logger konfigurieren
logging.basicConfig(handlers=[handler], level=logging.INFO)

# stdout/stderr umleiten
class LoggerWriter:
    def __init__(self, level):
        self.level = level
    
    def write(self, message):
        if message.strip():
            self.level(message.strip())
    
    def flush(self):
        pass

sys.stdout = LoggerWriter(logging.info)
sys.stderr = LoggerWriter(logging.error)

print("Flask WSGI gestartet")
from app import app as application
