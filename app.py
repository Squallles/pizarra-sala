from flask import Flask, request, jsonify, send_from_directory, session
from flask_cors import CORS
from flask_compress import Compress
import os, json
import psycopg2
from psycopg2.extras import RealDictCursor, execute_values
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'pizarra-sala-tlv-2026')
CORS(app, supports_credentials=True)
Compress(app)

DB_URL = os.environ.get('DATABASE_URL', '')

def get_db():
    return psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)

def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('''CREATE TABLE IF NOT EXISTS tp_users (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                is_admin BOOLEAN DEFAULT FALSE,
                active BOOLEAN DEFAULT TRUE
            )''')
            cur.execute('''CREATE TABLE IF NOT EXISTS notas (
                id SERIAL PRIMARY KEY,
                mes TEXT NOT NULL,
                tp_code TEXT NOT NULL,
                fecha TEXT NOT NULL,
                telefono TEXT NOT NULL,
                localidad TEXT DEFAULT '',
                provincia TEXT DEFAULT '',
                zona TEXT NOT NULL CHECK (zona IN ('resto', 'madrid')),
                situacion TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT NOW()
            )''')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_notas_mes ON notas(mes)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_notas_tp ON notas(tp_code)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_notas_mes_tp ON notas(mes, tp_code)')

            # Seed initial TPs if empty
            cur.execute('SELECT COUNT(*) as c FROM tp_users')
            if cur.fetchone()['c'] == 0:
                tps = [
                    ('30002', 'MAYTE', True),
                    ('30039', 'EVA', True),
                    ('10142', 'LAURA', False),
                    ('10132', 'AGUEDA', False),
                    ('10123', 'VIRGINIA', False),
                    ('10161', 'CHELO', False),
                    ('10004', 'SANDRA', False),
                    ('10160', 'NOEMI', False),
                    ('10165', 'BEATRIZ', False),
                    ('10164', 'NEREA', False),
                    ('10169', 'EVA CORDERO', False),
                    ('10170', 'SANDRA ARIAS', False),
                ]
                execute_values(cur,
                    'INSERT INTO tp_users (code, name, is_admin) VALUES %s ON CONFLICT DO NOTHING',
                    tps)
                print(f"Seed: {len(tps)} teleoperadoras creadas")
        conn.commit()
    print("BD inicializada")

try:
    init_db()
except Exception as e:
    print(f"Error init BD: {e}")

# ── AUTH ──────────────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_code' not in session:
            return jsonify({'error': 'No autenticado'}), 401
        return f(*args, **kwargs)
    return decorated

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json or {}
    code = data.get('code', '').strip()
    pwd = data.get('password', '').strip()
    if not code or not pwd:
        return jsonify({'error': 'Introduce tu codigo'}), 400
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT code, name, is_admin FROM tp_users WHERE code=%s AND active=TRUE', (code,))
            user = cur.fetchone()
    if not user or code != pwd:
        return jsonify({'error': 'Codigo o contrasena incorrectos'}), 401
    session['user_code'] = user['code']
    session['user_name'] = user['name']
    session['is_admin'] = user['is_admin']
    return jsonify({'ok': True, 'user': dict(user)})

@app.route('/api/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({'ok': True})

@app.route('/api/me')
def api_me():
    if 'user_code' not in session:
        return jsonify({'logged_in': False})
    return jsonify({'logged_in': True, 'code': session['user_code'],
                    'name': session['user_name'], 'is_admin': session.get('is_admin', False)})

# ── TPs ───────────────────────────────────────────────────────────────────────
@app.route('/api/tps')
@login_required
def api_tps():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT code, name, is_admin, active FROM tp_users ORDER BY name')
            rows = cur.fetchall()
    return jsonify(rows)

@app.route('/api/tps', methods=['POST'])
@login_required
def api_tp_create():
    if not session.get('is_admin'):
        return jsonify({'error': 'Solo admin'}), 403
    d = request.json
    code = d.get('code', '').strip()
    name = d.get('name', '').strip().upper()
    is_admin = d.get('is_admin', False)
    if not code or not name:
        return jsonify({'error': 'Codigo y nombre requeridos'}), 400
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('''INSERT INTO tp_users (code, name, is_admin) VALUES (%s, %s, %s)
                          ON CONFLICT (code) DO UPDATE SET name=EXCLUDED.name, is_admin=EXCLUDED.is_admin, active=TRUE''',
                       (code, name, is_admin))
        conn.commit()
    return jsonify({'ok': True})

@app.route('/api/tps/<code>', methods=['DELETE'])
@login_required
def api_tp_delete(code):
    if not session.get('is_admin'):
        return jsonify({'error': 'Solo admin'}), 403
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('UPDATE tp_users SET active=FALSE WHERE code=%s', (code,))
        conn.commit()
    return jsonify({'ok': True})

# ── NOTAS ─────────────────────────────────────────────────────────────────────
@app.route('/api/notas')
@login_required
def api_notas():
    mes = request.args.get('mes', '')
    if not mes:
        return jsonify({'error': 'mes requerido'}), 400
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('''SELECT n.*, t.name as tp_name FROM notas n
                          LEFT JOIN tp_users t ON t.code = n.tp_code
                          WHERE n.mes = %s ORDER BY n.fecha DESC, n.created_at DESC''', (mes,))
            rows = cur.fetchall()
    # Convert dates
    for r in rows:
        if r.get('created_at'):
            r['created_at'] = str(r['created_at'])
    return jsonify(rows)

@app.route('/api/notas', methods=['POST'])
@login_required
def api_nota_create():
    d = request.json
    tp_code = d.get('tp_code', '').strip()
    # Only own notes unless admin
    if tp_code != session['user_code'] and not session.get('is_admin'):
        return jsonify({'error': 'Solo puedes crear tus propias notas'}), 403
    mes = d.get('mes', '').strip()
    fecha = d.get('fecha', '').strip()
    telefono = d.get('telefono', '').strip()
    localidad = d.get('localidad', '').strip()
    provincia = d.get('provincia', '').strip()
    zona = d.get('zona', '').strip()
    situacion = d.get('situacion', '').strip()
    if not mes or not fecha or not telefono or zona not in ('resto', 'madrid'):
        return jsonify({'error': 'Faltan campos obligatorios'}), 400
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('''INSERT INTO notas (mes, tp_code, fecha, telefono, localidad, provincia, zona, situacion)
                          VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id''',
                       (mes, tp_code, fecha, telefono, localidad, provincia, zona, situacion))
            nota_id = cur.fetchone()['id']
        conn.commit()
    return jsonify({'ok': True, 'id': nota_id})

@app.route('/api/notas/<int:nota_id>', methods=['PUT'])
@login_required
def api_nota_update(nota_id):
    d = request.json
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT tp_code FROM notas WHERE id=%s', (nota_id,))
            nota = cur.fetchone()
            if not nota:
                return jsonify({'error': 'Nota no encontrada'}), 404
            if nota['tp_code'] != session['user_code'] and not session.get('is_admin'):
                return jsonify({'error': 'Solo puedes editar tus propias notas'}), 403
            fields = []
            values = []
            for col in ['fecha', 'telefono', 'localidad', 'provincia', 'zona', 'situacion']:
                if col in d:
                    fields.append(f"{col}=%s")
                    values.append(d[col])
            if fields:
                values.append(nota_id)
                cur.execute(f"UPDATE notas SET {','.join(fields)} WHERE id=%s", values)
            conn.commit()
    return jsonify({'ok': True})

@app.route('/api/notas/<int:nota_id>', methods=['DELETE'])
@login_required
def api_nota_delete(nota_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT tp_code FROM notas WHERE id=%s', (nota_id,))
            nota = cur.fetchone()
            if not nota:
                return jsonify({'error': 'Nota no encontrada'}), 404
            if nota['tp_code'] != session['user_code'] and not session.get('is_admin'):
                return jsonify({'error': 'Solo puedes eliminar tus propias notas'}), 403
            cur.execute('DELETE FROM notas WHERE id=%s', (nota_id,))
        conn.commit()
    return jsonify({'ok': True})

# ── RESUMEN ───────────────────────────────────────────────────────────────────
@app.route('/api/resumen')
@login_required
def api_resumen():
    mes = request.args.get('mes', '')
    if not mes:
        return jsonify({'error': 'mes requerido'}), 400
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('''
                SELECT tp_code, zona, situacion, COUNT(*) as cnt
                FROM notas WHERE mes = %s
                GROUP BY tp_code, zona, situacion
            ''', (mes,))
            rows = cur.fetchall()
            cur.execute('SELECT code, name FROM tp_users WHERE active=TRUE ORDER BY name')
            tps = cur.fetchall()

    # Build summary per TP
    resumen = {}
    for tp in tps:
        resumen[tp['code']] = {
            'code': tp['code'], 'name': tp['name'],
            'ventas': 0, 'resto': 0, 'madrid': 0, 'notas': 0,
            'nulos': 0, 'nulos_ruta': 0, 'pend': 0, 'bajas': 0, 'en_ruta': 0
        }

    for r in rows:
        code = r['tp_code']
        if code not in resumen:
            continue
        cnt = r['cnt']
        sit = (r['situacion'] or '').upper().strip()
        zona = r['zona']
        s = resumen[code]
        s['notas'] += cnt
        if zona == 'resto':
            s['resto'] += cnt
        elif zona == 'madrid':
            s['madrid'] += cnt

        if sit == 'INSTALACION' or sit == 'INSTALACIÓN':
            s['ventas'] += cnt
        elif sit == 'NULO':
            s['nulos'] += cnt
        elif sit == 'NULO RUTA':
            s['nulos_ruta'] += cnt
        elif sit == 'BAJA':
            s['bajas'] += cnt
        elif sit in ('EN RUTA', 'RUTA MAÑANA'):
            s['en_ruta'] += cnt
        else:
            s['pend'] += cnt

    # Sort by ventas desc
    lista = sorted(resumen.values(), key=lambda x: -x['ventas'])
    # Totals
    totals = {k: sum(r[k] for r in lista) for k in ['ventas','resto','madrid','notas','nulos','nulos_ruta','pend','bajas','en_ruta']}

    return jsonify({'resumen': lista, 'totals': totals})

# ── PROVINCIAS ────────────────────────────────────────────────────────────────
@app.route('/api/provincias')
@login_required
def api_provincias():
    mes = request.args.get('mes', '')
    if not mes:
        return jsonify({'error': 'mes requerido'}), 400
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('''
                SELECT provincia, situacion, COUNT(*) as cnt
                FROM notas WHERE mes = %s AND situacion != 'INSTALACION' AND situacion != 'INSTALACIÓN'
                GROUP BY provincia, situacion ORDER BY provincia
            ''', (mes,))
            rows = cur.fetchall()

    provs = {}
    for r in rows:
        p = r['provincia'] or 'SIN PROVINCIA'
        if p not in provs:
            provs[p] = {'pendiente': 0, 'en_ruta': 0, 'nulo': 0, 'nulo_ruta': 0, 'baja': 0}
        sit = (r['situacion'] or '').upper().strip()
        if sit in ('EN RUTA', 'RUTA MAÑANA'):
            provs[p]['en_ruta'] += r['cnt']
        elif sit == 'NULO':
            provs[p]['nulo'] += r['cnt']
        elif sit == 'NULO RUTA':
            provs[p]['nulo_ruta'] += r['cnt']
        elif sit == 'BAJA':
            provs[p]['baja'] += r['cnt']
        else:
            provs[p]['pendiente'] += r['cnt']

    return jsonify(provs)

# ── MESES DISPONIBLES ─────────────────────────────────────────────────────────
@app.route('/api/meses')
@login_required
def api_meses():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT DISTINCT mes FROM notas ORDER BY mes DESC')
            rows = cur.fetchall()
    meses = [r['mes'] for r in rows]
    # Ensure current month is always in the list
    from datetime import datetime
    import pytz
    ahora = datetime.now(pytz.timezone('Europe/Madrid'))
    current = ahora.strftime('%Y-%m')
    if current not in meses:
        meses.insert(0, current)
    return jsonify(meses)

# ── STATIC ────────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)
