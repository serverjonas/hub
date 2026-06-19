# toolbox/email.py
"""Versendet E-Mails \u00fcber das lokal installierte `msmtp` (SMTP-Relay).

Das Modul ruft `msmtp -t` als Subprozess auf und \u00fcbergibt eine vollst\u00e4ndige
RFC822-Nachricht \u00fcber stdin. Damit msmtp die Empf\u00e4nger findet, MUSS die
Nachricht mindestens einen `To:`-Header enthalten.

Sicherheit:
- KEIN `shell=True` (Args werden als Liste \u00fcbergeben)
- Es wird ein minimaler, bereinigter Env-Kontext \u00fcbergeben (nur PATH + HOME),
  damit sensible ENV-Variablen wie `SECRET_KEY` nicht an msmtp durchgereicht werden.
"""

import logging
import os
import socket
import subprocess
from email.utils import format_datetime
from email.message import EmailMessage
from datetime import datetime, timezone
from urllib.parse import urlparse

log = logging.getLogger("email")


def _mail_from():
    return os.environ.get("MAIL_FROM", "no-reply@serverjonas.local")


def _public_base_url():
    """Liefert die \u00f6ffentliche Basis-URL der App aus PUBLIC_BASE_URL oder f\u00e4llt
    auf eine sinnvolle Default-Adresse zur\u00fcck, sofern nicht konfiguriert."""
    return os.environ.get("PUBLIC_BASE_URL", "http://localhost:5000")


def _safe_env():
    """Stark reduziertes Env f\u00fcr den Subprozess; vermeidet Leaks sensibler Vars."""
    safe = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
    home = os.environ.get("HOME")
    if home:
        safe["HOME"] = home
    lang = os.environ.get("LANG")
    if lang:
        safe["LANG"] = lang
    return safe


def build_message(to_address, subject, text_body):
    msg = EmailMessage()
    msg["From"] = _mail_from()
    msg["To"] = to_address
    msg["Subject"] = subject
    msg["Date"] = format_datetime(datetime.now(timezone.utc))
    msg.set_content(text_body, subtype="plain", charset="utf-8")
    return msg


def _rfc_bytes(msg):
    # EmailMessage.as_bytes schreibt komplette RFC822-Bytes inkl. Header-Trennzeile.
    return msg.as_bytes(policy=None)


def send_email(to_address, subject, text_body, timeout=8):
    """Versendet eine Nur-Text-Mail via msmtp.

    Liefert (True, None) bei Erfolg, sonst (False, Fehlermeldung).
    """
    if not to_address or "@" not in to_address:
        return False, "Ung\u00fcltige Empf\u00e4nger-Adresse"

    msg = build_message(to_address, subject, text_body)
    payload = _rfc_bytes(msg)

    cmd = ["msmtp", "-t"]

    try:
        proc = subprocess.run(
            cmd,
            input=payload,
            capture_output=True,
            env=_safe_env(),
            shell=False,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return False, "msmtp ist auf dem Server nicht installiert"
    except subprocess.TimeoutExpired:
        return False, "msmtp-Timeout"
    except Exception as e:
        return False, f"msmtp-Aufruf fehlgeschlagen: {e}"

    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", "replace").strip()
        log.warning("msmtp failed for %s (rc=%s): %s", to_address, proc.returncode, err)
        return False, err or f"msmtp beendete sich mit Code {proc.returncode}"

    log.info("mail sent to=%s subject=%r host=%s",
             to_address, subject, socket.gethostname())
    return True, None


def verification_email_body(username, verify_url, ttl_hours=24):
    """Plain-Text-Body f\u00fcr die Verifizierungs-Mail (deutschsprachig)."""
    return (
        f"Hallo {username},\n\n"
        f"bitte best\u00e4tige deine E-Mail-Adresse, um den serverjonas-Hub "
        f"freizuschalten. Klicke dazu innerhalb der n\u00e4chsten {ttl_hours} Stunden "
        f"auf den folgenden Link:\n\n"
        f"   {verify_url}\n\n"
        f"Wenn du diesen Account nicht angefordert hast, ignoriere diese Nachricht "
        f"einfach \u2013 es ist dann nichts weiter zu tun. Aus Sicherheitsgr\u00fcnden "
        f"wird der Link mit einem Einmal-Token gesch\u00fctzt.\n\n"
        f"\u2014 serverjonas\n"
    )


def build_verify_url(token):
    base = _public_base_url().rstrip("/")
    return f"{base}/hub/email/verify?token={token}"
