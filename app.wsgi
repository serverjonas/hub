import sys
sys.path.insert(0, "/var/www/serverjonas-hub")
sys.stdout = sys.stderr
print("Flask WSGI gestartet")
from app import app as application
