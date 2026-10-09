"""R39: the email channel goes through the SSRF guard and verifies TLS."""
import socket
import threading

import pytest

from cronsentinel import netguard
from cronsentinel.config import settings


@pytest.fixture
def fake_smtp():
    """Minimal SMTP greeter on 127.0.0.1 — enough for smtplib to connect and QUIT."""
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(5)
    port = srv.getsockname()[1]
    hits = []

    def loop():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            hits.append(1)
            c.sendall(b"220 fake ESMTP\r\n")
            f = c.makefile("rb")
            for line in f:
                cmd = line.strip().upper()
                if cmd.startswith(b"QUIT"):
                    c.sendall(b"221 bye\r\n"); break
                c.sendall(b"250 ok\r\n")
            c.close()
    threading.Thread(target=loop, daemon=True).start()
    yield port, hits
    srv.close()


def test_smtp_to_loopback_is_blocked_by_default(fake_smtp, monkeypatch):
    port, hits = fake_smtp
    monkeypatch.setattr(settings, "outbound_allow_private", False)
    for host in ("127.0.0.1", "localhost"):
        with pytest.raises(netguard.BlockedDestination):
            netguard.smtp(host, port, timeout=2)
    assert hits == [], "no TCP connection may reach the blocked target"


def test_smtp_metadata_and_private_hosts_rejected_at_config_time(monkeypatch):
    monkeypatch.setattr(settings, "outbound_allow_private", False)
    for host in ("169.254.169.254", "10.0.0.5", "localhost", "[::1]", "a/b", "x@y"):
        with pytest.raises(netguard.BlockedDestination):
            netguard.check_host(host, 25)


def test_smtp_allowed_when_private_permitted(fake_smtp, monkeypatch):
    port, hits = fake_smtp
    monkeypatch.setattr(settings, "outbound_allow_private", True)
    with netguard.smtp("127.0.0.1", port, timeout=2) as s:
        assert s.noop()[0] == 250
    assert hits == [1]


def test_tenant_error_categorises_smtp_failures():
    import smtplib
    assert netguard.tenant_error(netguard.BlockedDestination("blocked: x")) == "destination not allowed"
    assert netguard.tenant_error(smtplib.SMTPAuthenticationError(535, b"no")) == "SMTP authentication failed"
    assert netguard.tenant_error(ConnectionRefusedError("10.0.0.1:25 refused")) == "connection failed"
