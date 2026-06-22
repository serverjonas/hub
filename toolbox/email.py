# toolbox/email.py
"""Versendet E-Mails über das lokal installierte `msmtp` (SMTP-Relay).

Das Modul ruft `msmtp -t` als Subprozess auf und übergibt eine vollständige
RFC822-Nachricht über stdin. Damit msmtp die Empfänger findet, MUSS die
Nachricht mindestens einen `To:`-Header enthalten.

Sicherheit:
- KEIN `shell=True` (Args werden als Liste übergeben)
- Es wird ein minimaler, bereinigter Env-Kontext übergeben (nur PATH + HOME),
  damit sensible ENV-Variablen wie `SECRET_KEY` nicht an msmtp durchgereicht werden.

Debug-Modus:
- Wenn `$DEBUG_NO_EMAIL=1` gesetzt ist (von `app.py --debug` aktiviert), wird
  KEIN msmtp-Aufruf getätigt. Die Mail wird stattdessen geloggt und es wird
  ``(True, None)`` zurückgegeben, damit der Verifizierungs-Flow in Dev/Test
  ohne echten SMTP-Relay durchläuft.
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
    return os.environ.get("MAIL_FROM", "support@serverjonas.de")


def _public_base_url():
    """Liefert die öffentliche Basis-URL der App aus PUBLIC_BASE_URL oder fällt
    auf eine sinnvolle Default-Adresse zurück, sofern nicht konfiguriert."""
    return os.environ.get("PUBLIC_BASE_URL", "https://serverjonas.de")


def _safe_env():
    """Stark reduziertes Env für den Subprozess; vermeidet Leaks sensibler Vars."""
    safe = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
    home = os.environ.get("HOME")
    if home:
        safe["HOME"] = home
    lang = os.environ.get("LANG")
    if lang:
        safe["LANG"] = lang
    return safe


def _is_debug_no_email() -> bool:
    """True wenn die App im Debug-Modus läuft und Mails nicht versendet werden.

    Gesteuert über $DEBUG_NO_EMAIL (wird von app.py beim --debug-Flag gesetzt).
    Damit kann der Verifizierungs-Flow lokal trotzdem durchlaufen, ohne dass
    tatsächlich Mails verschickt werden.
    """
    return os.environ.get("DEBUG_NO_EMAIL", "").strip().lower() in ("1", "true", "yes")


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

    Liefert (True, None) bei Erfolg, sonst (False, Fehlermeldung). Im Debug-Modus
    ($DEBUG_NO_EMAIL=1) wird der Versand übersprungen und das Mail lokal geloggt,
    damit die Verifizierungs-Route in Dev/Test trotzdem „OK“ zurückgibt.
    """
    if not to_address or "@" not in to_address:
        return False, "Ungültige Empfänger-Adresse"

    if _is_debug_no_email():
        log.warning(
            "DEBUG_NO_EMAIL=1 — E-Mail nicht versendet. Inhalt wird geloggt:\n"
            "  To:      %s\n"
            "  Subject: %s\n"
            "  Body:    %s",
            to_address, subject, (text_body or "")[:400],
        )
        print(
            f"\033[33m[email debug]\033[0m würde zu={to_address} "
            f"\033[90msubject=\033[0m{subject!r} "
            f"\033[90mbody=(…)\033[0m"
        )
        return True, None

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
    """Plain-Text-Body für die Verifizierungs-Mail (deutschsprachig)."""
    return (
        f"Hallo {username},\n\n"
        f"bitte bestätige deine E-Mail-Adresse, um den serverjonas-Hub "
        f"freizuschalten. Klicke dazu innerhalb der nächsten {ttl_hours} Stunden "
        f"auf den folgenden Link:\n\n"
        f"   {verify_url}\n\n"
        f"Wenn du diesen Account nicht angefordert hast, ignoriere diese Nachricht "
        f"einfach – es ist dann nichts weiter zu tun. Aus Sicherheitsgründen "
        f"wird der Link mit einem Einmal-Token geschützt.\n\n"
        f"— serverjonas\n"
    )


def build_verify_url(token):
    base = _public_base_url().rstrip("/")
    return f"{base}/hub/email/verify?token={token}"
