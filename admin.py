"""Administración local de cuentas y responsables de Repara."""

import getpass, sys
from server import CITIES, connection, exec_ignore, init, now, password_hash


def usage():
    print("Uso:")
    print("  python admin.py user add <usuario> <Rivera|Santana do Livramento>")
    print("  python admin.py user list")
    print("  python admin.py user owner <usuario>")
    print("  python admin.py user reset <usuario>")
    print("  python admin.py responsible add <Rivera|Santana do Livramento> <nombre>")
    print("  python admin.py responsible list")
    raise SystemExit(2)


def main():
    init()
    args = sys.argv[1:]
    if len(args) >= 2 and args[:2] == ["user", "add"]:
        if len(args) < 4:
            usage()
        username = args[2].strip()
        city = " ".join(args[3:]).strip()
        if city not in CITIES:
            raise SystemExit("Ciudad inválida. Use Rivera o Santana do Livramento.")
        p1 = getpass.getpass("Contraseña: ")
        p2 = getpass.getpass("Repetir contraseña: ")
        if p1 != p2 or len(p1) < 12:
            raise SystemExit(
                "Las contraseñas no coinciden o tienen menos de 12 caracteres."
            )
        salt, digest = password_hash(p1)
        with connection() as con:
            con.execute(
                "INSERT INTO admin_users(username,password_salt,password_hash,city,created_at) VALUES (?,?,?,?,?)",
                (username, salt, digest, city, now()),
            )
        print(f"Cuenta {username} creada para {city}.")
    elif len(args) == 3 and args[:2] == ["user", "reset"]:
        username = args[2].strip().lower()
        p1 = getpass.getpass("Nueva contraseña: ")
        p2 = getpass.getpass("Repetir contraseña: ")
        if p1 != p2 or len(p1) < 12:
            raise SystemExit("Usá contraseñas coincidentes de al menos 12 caracteres.")
        salt, digest = password_hash(p1)
        with connection() as con:
            row = con.execute(
                "SELECT username FROM admin_users WHERE LOWER(username)=LOWER(?)",
                (username,),
            ).fetchone()
            if not row:
                raise SystemExit("Usuario no encontrado.")
            con.execute(
                "UPDATE admin_users SET password_salt=?,password_hash=?,active=1 WHERE username=?",
                (salt, digest, row["username"]),
            )
            con.execute(
                "DELETE FROM admin_sessions WHERE username=?", (row["username"],)
            )
        print("Contraseña restablecida y sesiones revocadas.")
    elif len(args) == 3 and args[:2] == ["user", "owner"]:
        with connection() as con:
            changed = con.execute(
                "UPDATE admin_users SET is_owner=1 WHERE LOWER(username)=LOWER(?) AND active=1",
                (args[2],),
            )
            if not changed.rowcount:
                raise SystemExit("Usuario activo no encontrado.")
        print("Permiso de propietario concedido. Volvé a iniciar sesión.")
    elif args == ["user", "list"]:
        with connection() as con:
            for r in con.execute(
                "SELECT username,city,active FROM admin_users ORDER BY city,username"
            ):
                print(
                    f"{r['username']} | {r['city']} | {'activa' if r['active'] else 'inactiva'}"
                )
    elif len(args) >= 4 and args[:2] == ["responsible", "add"]:
        city = args[2]
        name = " ".join(args[3:]).strip()
        if city not in CITIES:
            raise SystemExit(
                'Para nombres con espacios en la ciudad use comillas: "Santana do Livramento".'
            )
        with connection() as con:
            exec_ignore(
                con,
                "INSERT OR IGNORE INTO assignees(city,name) VALUES (?,?)",
                (city, name),
            )
        print(f"Responsable agregado en {city}: {name}")
    elif args == ["responsible", "list"]:
        with connection() as con:
            for r in con.execute(
                "SELECT city,name,active FROM assignees ORDER BY city,id"
            ):
                print(
                    f"{r['city']} | {r['name']} | {'activo' if r['active'] else 'inactivo'}"
                )
    else:
        usage()


if __name__ == "__main__":
    main()
