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

import html
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


def build_message(to_address, subject, text_body, html_body=None):
    """Baut eine RFC822-Nachricht.

    Ohne ``html_body`` wird eine reine Plain-Text-Mail erzeugt (wie bisher).
    Mit ``html_body`` wird eine ``multipart/alternative``-Mail erstellt, in der
    der Plain-Text-Teil als Fallback für Clients ohne HTML-Support dient und
    HTML bevorzugt dargestellt wird.
    """
    msg = EmailMessage()
    msg["From"] = _mail_from()
    msg["To"] = to_address
    msg["Subject"] = subject
    msg["Date"] = format_datetime(datetime.now(timezone.utc))
    msg.set_content(text_body, subtype="plain", charset="utf-8")
    if html_body:
        msg.add_alternative(html_body, subtype="html", charset="utf-8")
    return msg


def _rfc_bytes(msg):
    # EmailMessage.as_bytes schreibt komplette RFC822-Bytes inkl. Header-Trennzeile.
    return msg.as_bytes(policy=None)


def send_email(to_address, subject, text_body, html_body=None, timeout=8):
    """Versendet eine Mail via msmtp.

    Wird ``html_body`` übergeben, wird die Mail als ``multipart/alternative``
    verschickt: HTML ist die bevorzugte Darstellung, Plain-Text dient als
    Fallback für Clients ohne HTML-Rendering. Ohne ``html_body`` wird – wie
    bisher – eine reine Plain-Text-Mail versendet.

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
            "  Body:    %s\n"
            "  HTML:    %s",
            to_address, subject,
            (text_body or "")[:400],
            (html_body or "")[:400],
        )
        print(
            f"\033[33m[email debug]\033[0m würde zu={to_address} "
            f"\033[90msubject=\033[0m{subject!r} "
            f"\033[90mbody=(…)\033[0m"
            f"\033[90m html=(…)\033[0m"
        )
        return True, None

    msg = build_message(to_address, subject, text_body, html_body=html_body)
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
    """Plain-Text-Body für die Verifizierungs-Mail (deutschsprachig).

    Dient als Fallback-Part innerhalb der ``multipart/alternative``-Mail,
    damit Clients ohne HTML-Rendering weiterhin einen lesbaren Text erhalten.
    """
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


# HTML-Template für die Verifizierungs-Mail. ``{greeting}`` und
# ``{verify_url}`` werden beim Rendern via html.escape(...) neutralisiert, damit
# ein böswillig gewählter Account-Name oder ein manipuliertes Token keinen
# HTML-/JS-Payload in den Mail-Client der Empfänger schleusen kann.
_VERIFICATION_HTML_TEMPLATE = (
    "<html>\n"
    '  <body style="margin:0; font-family:Arial, sans-serif; background:#0b0f14; color:#e6e6e6;">\n'
    '    <div style="max-width:600px; margin:40px auto; padding:24px; background:#111826; border-radius:12px;">\n'
    "      \n"
    '      <div style="text-align:center; margin-bottom:24px;">\n'
    '        <img src="https://serverjonas.de/static/logo.png" alt="serverjonas" style="width:120px;">\n'
    "      </div>\n"
    "      \n"
    '      <h2 style="text-align:center;">E-Mail bestätigen</h2>\n'
    "      \n"
    "      <p>{greeting}</p>\n"
    "      \n"
    "      <p>klicke auf den Button, um deinen Account zu aktivieren:</p>\n"
    "      \n"
    '      <div style="text-align:center; margin:30px 0;">\n'
    '        <a href="{verify_url}"\n'
    '           style="background:#4f7cff; color:white; padding:12px 20px; border-radius:8px; text-decoration:none; display:inline-block;">\n'
    "          Account bestätigen\n"
    "        </a>\n"
    "      </div>\n"
    "      \n"
    '      <p style="font-size:12px; opacity:0.7;">\n'
    "        Falls du keinen Account erstellt hast, kannst du diese Mail ignorieren.\n"
    "      </p>\n"
    "      \n"
    '      <hr style="border:0; border-top:1px solid #2a2f3a; margin:20px 0;">\n'
    "      \n"
    '      <p style="font-size:11px; opacity:0.5; text-align:center;">\n'
    "        serverjonas network\n"
    "      </p>\n"
    "      \n"
    "    </div>\n"
    "  </body>\n"
    "</html>"
)


def verification_email_body_html(username, verify_url, ttl_hours=24):
    """HTML-Body für die Verifizierungs-Mail (deutschsprachig, serverjonas-Look).

    Wird als der vom Mail-Client bevorzugte Part der ``multipart/alternative``
    verwendet. Dazu muss ``send_email`` mit ``html_body=...`` aufgerufen werden.

    ``username`` und ``verify_url`` werden via :func:`html.escape` neutralisiert,
    damit ein böswillig gewählter Account-Name (z.B. ``<img src=x onerror=…>``)
    oder ein manipuliertes Token keinen HTML-/JS-Payload in den Mail-Client
    der Empfänger schleusen kann. ``ttl_hours`` bleibt Teil der Signatur, hat
    hier aber keine sichtbare Auswirkung – die zeitliche Begrenzung steckt im
    Verify-Token selbst und wird im Plain-Text-Part erwähnt; im HTML-Layout
    ist sie bewusst weggelassen, damit der CTA-Button kompakt bleibt.
    """
    safe_greeting = "Hallo " + html.escape(str(username or ""), quote=False)
    safe_url = html.escape(str(verify_url or ""), quote=True)
    return _VERIFICATION_HTML_TEMPLATE.format(
        greeting=safe_greeting,
        verify_url=safe_url,
    )


def build_verify_url(token):
    base = _public_base_url().rstrip("/")
    return f"{base}/hub/email/verify?token={token}"
