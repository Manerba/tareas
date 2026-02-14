"""
Tareas - LDAP Hilfsfunktionen
Verbindung, Gruppen, Mitglieder, Authentifizierung via ldap3.
"""

from ldap3 import Server, Connection, NONE, SUBTREE, Tls
import ssl


def _create_server(server_addr, port, use_ssl, verify_cert=False):
    """Erstellt ein ldap3.Server-Objekt. TLS-Objekt immer dabei (fuer StartTLS oder LDAPS)."""
    if verify_cert:
        tls = Tls(validate=ssl.CERT_REQUIRED)
    else:
        tls = Tls(validate=ssl.CERT_NONE)
    return Server(server_addr, port=port, use_ssl=bool(use_ssl), tls=tls,
                  get_info=NONE, connect_timeout=10)


def _bind_connection(server, bind_dn, bind_password, use_ssl, receive_timeout=10):
    """Erstellt und bindet eine Connection. Bei nicht-SSL wird StartTLS verwendet."""
    conn = Connection(server, user=bind_dn, password=bind_password,
                      auto_bind=False, receive_timeout=receive_timeout,
                      raise_exceptions=True)
    conn.open()
    if not use_ssl:
        conn.start_tls()
    conn.bind()
    return conn


def test_connection(server_addr, port, use_ssl, bind_dn, bind_password, search_base, verify_cert=False):
    """
    Verbindungstest mit den angegebenen Parametern.
    Liefert (True, None) bei Erfolg, (False, Fehlermeldung) bei Fehler.
    """
    try:
        srv = _create_server(server_addr, port, use_ssl, verify_cert=verify_cert)
        conn = _bind_connection(srv, bind_dn, bind_password, use_ssl)
        # Teste ob search_base erreichbar ist
        conn.search(search_base, '(objectClass=*)', search_scope=SUBTREE,
                    size_limit=1, attributes=[])
        conn.unbind()
        return True, None
    except Exception as e:
        return False, str(e)


def list_groups(config):
    """
    Alle Gruppen im search_base auflisten.
    config: dict mit server, port, use_ssl, bind_dn, bind_password, search_base
    Liefert Liste von {dn, name}.
    """
    try:
        srv = _create_server(config["server"], config["port"], config["use_ssl"],
                             verify_cert=config.get("verify_cert", False))
        conn = _bind_connection(srv, config["bind_dn"], config["bind_password"],
                                config["use_ssl"])
        conn.search(
            config["search_base"],
            '(objectClass=group)',
            search_scope=SUBTREE,
            attributes=['cn', 'distinguishedName'],
        )
        groups = []
        for entry in conn.entries:
            groups.append({
                "dn": str(entry.distinguishedName),
                "name": str(entry.cn),
            })
        conn.unbind()
        groups.sort(key=lambda g: g["name"].lower())
        return groups
    except Exception as e:
        raise RuntimeError(f"Gruppen konnten nicht geladen werden: {e}")


def get_group_members(config):
    """
    Alle Benutzer der konfigurierten Gruppe abrufen.
    config: dict mit server, port, use_ssl, bind_dn, bind_password, search_base, group_dn
    Liefert Liste von {dn, sAMAccountName, sn, givenName, mail}.
    """
    try:
        srv = _create_server(config["server"], config["port"], config["use_ssl"],
                             verify_cert=config.get("verify_cert", False))
        conn = _bind_connection(srv, config["bind_dn"], config["bind_password"],
                                config["use_ssl"], receive_timeout=30)

        # Mitglieder der Gruppe abrufen
        conn.search(
            config["group_dn"],
            '(objectClass=group)',
            search_scope=SUBTREE,
            attributes=['member'],
        )

        if not conn.entries:
            conn.unbind()
            return []

        members_dns = conn.entries[0].member.values if conn.entries[0].member else []

        members = []
        for member_dn in members_dns:
            conn.search(
                str(member_dn),
                '(objectClass=user)',
                search_scope=SUBTREE,
                attributes=['sAMAccountName', 'sn', 'givenName', 'mail', 'distinguishedName'],
            )
            if conn.entries:
                entry = conn.entries[0]
                members.append({
                    "dn": str(entry.distinguishedName),
                    "sAMAccountName": str(entry.sAMAccountName) if entry.sAMAccountName else "",
                    "sn": str(entry.sn) if entry.sn else "",
                    "givenName": str(entry.givenName) if entry.givenName else "",
                    "mail": str(entry.mail) if entry.mail else "",
                })

        conn.unbind()
        return members
    except Exception as e:
        raise RuntimeError(f"Gruppenmitglieder konnten nicht geladen werden: {e}")


def authenticate_user(config, username, password):
    """
    LDAP-Bind mit Benutzer-DN.
    1. DN des Benutzers anhand sAMAccountName finden (mit Service-Account)
    2. Bind-Versuch mit gefundenem DN + eingegebenem Passwort
    Liefert (True, None) bei Erfolg, (False, Fehlermeldung) bei Fehler.
    """
    try:
        srv = _create_server(config["server"], config["port"], config["use_ssl"],
                             verify_cert=config.get("verify_cert", False))

        # Service-Account: DN des Benutzers suchen
        conn = _bind_connection(srv, config["bind_dn"], config["bind_password"],
                                config["use_ssl"])

        # Domain-Root fuer Benutzersuche (Search-Base kann auf OU=Gruppen zeigen)
        domain_root = _get_domain_root(config["search_base"])
        search_filter = f'(&(objectClass=user)(sAMAccountName={_escape_ldap_filter(username)}))'
        conn.search(
            domain_root,
            search_filter,
            search_scope=SUBTREE,
            attributes=['distinguishedName'],
        )

        if not conn.entries:
            conn.unbind()
            return False, (f"Benutzer '{username}' im LDAP nicht gefunden. "
                           f"Search-Base: {domain_root}, Filter: {search_filter}")

        user_dn = str(conn.entries[0].distinguishedName)
        conn.unbind()

        # Bind mit Benutzer-Credentials
        try:
            user_conn = _bind_connection(srv, user_dn, password, config["use_ssl"])
            user_conn.unbind()
            return True, None
        except Exception as bind_err:
            return False, f"Bind fehlgeschlagen fuer DN '{user_dn}': {bind_err}"

    except Exception as e:
        error_msg = str(e)
        return False, f"LDAP-Authentifizierung fehlgeschlagen: {error_msg}"


def _get_domain_root(search_base):
    """Extrahiert den Domain-Root (DC-Komponenten) aus einem DN.
    z.B. 'OU=Gruppen,DC=himel,DC=local' -> 'DC=himel,DC=local'"""
    parts = [p.strip() for p in search_base.split(',')]
    dc_parts = [p for p in parts if p.upper().startswith('DC=')]
    return ','.join(dc_parts) if dc_parts else search_base


def _escape_ldap_filter(value):
    """Escaped Sonderzeichen fuer LDAP-Filter (RFC 4515)."""
    result = ""
    for char in value:
        if char == '\\':
            result += '\\5c'
        elif char == '*':
            result += '\\2a'
        elif char == '(':
            result += '\\28'
        elif char == ')':
            result += '\\29'
        elif char == '\x00':
            result += '\\00'
        else:
            result += char
    return result
