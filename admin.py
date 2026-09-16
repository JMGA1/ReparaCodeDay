"""Administración local de cuentas y responsables de Repara."""
import getpass, sys
from server import CITIES, connection, exec_ignore, init, now, password_hash

def usage():
    print('Uso:')
    print('  python admin.py user add <usuario> <Rivera|Santana do Livramento>')
    print('  python admin.py user list')
    print('  python admin.py responsible add <Rivera|Santana do Livramento> <nombre>')
    print('  python admin.py responsible list')
    raise SystemExit(2)

def main():
    init(); args=sys.argv[1:]
    if len(args)>=2 and args[:2]==['user','add']:
        if len(args)<4:usage()
        username=args[2].strip(); city=' '.join(args[3:]).strip()
        if city not in CITIES: raise SystemExit('Ciudad inválida. Use Rivera o Santana do Livramento.')
        p1=getpass.getpass('Contraseña: '); p2=getpass.getpass('Repetir contraseña: ')
        if p1!=p2 or len(p1)<8: raise SystemExit('Las contraseñas no coinciden o tienen menos de 8 caracteres.')
        salt,digest=password_hash(p1)
        with connection() as con: con.execute('INSERT INTO admin_users(username,password_salt,password_hash,city,created_at) VALUES (?,?,?,?,?)',(username,salt,digest,city,now()))
        print(f'Cuenta {username} creada para {city}.')
    elif args==['user','list']:
        with connection() as con:
            for r in con.execute('SELECT username,city,active FROM admin_users ORDER BY city,username'):print(f"{r['username']} | {r['city']} | {'activa' if r['active'] else 'inactiva'}")
    elif len(args)>=4 and args[:2]==['responsible','add']:
        city=args[2]; name=' '.join(args[3:]).strip()
        if city not in CITIES: raise SystemExit('Para nombres con espacios en la ciudad use comillas: "Santana do Livramento".')
        with connection() as con:exec_ignore(con,'INSERT OR IGNORE INTO assignees(city,name) VALUES (?,?)',(city,name))
        print(f'Responsable agregado en {city}: {name}')
    elif args==['responsible','list']:
        with connection() as con:
            for r in con.execute('SELECT city,name,active FROM assignees ORDER BY city,id'):print(f"{r['city']} | {r['name']} | {'activo' if r['active'] else 'inactivo'}")
    else:usage()
if __name__=='__main__':main()
