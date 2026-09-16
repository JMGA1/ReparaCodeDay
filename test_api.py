"""Pruebas de integración de Ciudad Visible v8."""

import json, os, socket, subprocess, sys, tempfile, time, unittest, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).parent


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cls.port = s.getsockname()[1]
        cls.url = f"http://127.0.0.1:{cls.port}"
        env = dict(
            os.environ,
            DATA_DIR=cls.tmp.name,
            PORT=str(cls.port),
            DATABASE_URL="",
            DEMO_MODE="0",
            OPENAI_API_KEY="",
        )
        setup = "from server import init,connection,password_hash,now;init();s,h=password_hash('clave-segura');c=connection();c.execute(\"INSERT INTO admin_users(username,password_salt,password_hash,city,created_at) VALUES (?,?,?,?,?)\",('rivera',s,h,'Rivera',now()));c.commit();c.close()"
        subprocess.check_call([sys.executable, "-c", setup], cwd=ROOT, env=env)
        cls.log = tempfile.TemporaryFile()
        cls.proc = subprocess.Popen(
            [sys.executable, "server.py"],
            cwd=ROOT,
            env=env,
            stdout=cls.log,
            stderr=cls.log,
        )
        for _ in range(60):
            try:
                urllib.request.urlopen(cls.url + "/api/health", timeout=1)
                break
            except OSError:
                time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait()
        cls.log.close()
        cls.tmp.cleanup()

    def req(self, path, body=None, method="GET", token=None):
        h = {}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        if token:
            h["Authorization"] = "Bearer " + token
        try:
            with urllib.request.urlopen(
                urllib.request.Request(
                    self.url + path, data=data, method=method, headers=h
                )
            ) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def create(
        self,
        rid,
        description="Hay un bache grande en el asfalto",
        lat=-30.905,
        lng=-55.55,
    ):
        return self.req(
            "/api/incidents",
            dict(
                request_id=rid,
                device="d-" + rid,
                description=description,
                lat=lat,
                lng=lng,
            ),
            "POST",
        )

    def test_simple_report_is_private_until_admin_approval(self):
        st, new = self.create("r1")
        self.assertEqual(st, 201)
        self.assertTrue(new["code"].startswith("RIV-"))
        self.assertFalse(
            any(x["id"] == new["id"] for x in self.req("/api/incidents")[1])
        )
        st, sub = self.req(
            f"/api/incidents/{new['id']}/subscribe",
            {"email": "persona@example.com"},
            "POST",
        )
        self.assertEqual(st, 200)
        self.assertTrue(sub["added"])
        st, login = self.req(
            "/api/login", {"username": "rivera", "password": "clave-segura"}, "POST"
        )
        token = login["token"]
        admin_rows = self.req("/api/admin/incidents", token=token)[1]
        row = next(x for x in admin_rows if x["id"] == new["id"])
        self.assertEqual(row["status"], "Recibido")
        st, _ = self.req(
            f"/api/incidents/{row['id']}",
            {
                "status": "En revisión",
                "assignee": "Sin asignar",
                "priority": row["priority"],
                "note": "Validada para publicación",
                "revision": row["revision"],
            },
            "PATCH",
            token,
        )
        self.assertEqual(st, 200)
        public_rows = self.req("/api/incidents")[1]
        row = next(x for x in public_rows if x["id"] == new["id"])
        self.assertEqual(row["status"], "En revisión")
        st, c = self.req(
            f"/api/incidents/{row['id']}/confirm", {"device": "otro"}, "POST"
        )
        self.assertEqual(st, 200)
        self.assertTrue(c["added"])

    def test_city_isolation_and_assignee_validation(self):
        self.create("r2", "Hay basura acumulada junto al contenedor", -30.905, -55.55)
        _, liv_created = self.create("s2", "Poste de luz apagado", -30.886, -55.53)
        st, login = self.req(
            "/api/login", {"username": "rivera", "password": "clave-segura"}, "POST"
        )
        self.assertEqual(st, 200)
        token = login["token"]
        st, rows = self.req("/api/admin/incidents", token=token)
        self.assertEqual(st, 200)
        self.assertTrue(rows)
        self.assertEqual({x["city"] for x in rows}, {"Rivera"})
        st, names = self.req("/api/admin/assignees", token=token)
        self.assertIn("Cuadrilla de mantenimiento", names)
        row = rows[0]
        self.assertEqual(
            self.req(
                f"/api/incidents/{row['id']}",
                {
                    "status": "En proceso",
                    "assignee": "Cuadrilla de mantenimiento",
                    "note": "OK",
                    "revision": row["revision"],
                },
                "PATCH",
                token,
            )[0],
            200,
        )
        self.assertEqual(
            self.req(
                f"/api/incidents/{liv_created['id']}",
                {
                    "status": "En proceso",
                    "assignee": "Cuadrilla de mantenimiento",
                    "note": "No",
                    "revision": 1,
                },
                "PATCH",
                token,
            )[0],
            403,
        )

    def test_ai_fallback_priority_and_duplicate_detection(self):
        st, first = self.create(
            "ai-1",
            "Hay un semáforo apagado y peligroso en esta esquina",
            -30.904,
            -55.549,
        )
        self.assertEqual(st, 201)
        self.assertIn(first["priority"], ("Alta", "Urgente"))
        self.assertIn(first["ai_source"], ("fallback", "openai"))
        token = self.req(
            "/api/login", {"username": "rivera", "password": "clave-segura"}, "POST"
        )[1]["token"]
        row = next(
            x
            for x in self.req("/api/admin/incidents", token=token)[1]
            if x["id"] == first["id"]
        )
        self.assertEqual(
            self.req(
                f"/api/incidents/{first['id']}",
                {
                    "status": "En revisión",
                    "assignee": "Sin asignar",
                    "priority": row["priority"],
                    "note": "Aprobada",
                    "revision": row["revision"],
                },
                "PATCH",
                token,
            )[0],
            200,
        )
        st, dup = self.create(
            "ai-2",
            "El semáforo de esta esquina sigue apagado y no funciona",
            -30.90402,
            -55.54902,
        )
        self.assertEqual(st, 409)
        self.assertEqual(dup["error"], "possible_duplicate")
        self.assertEqual(dup["duplicate"]["id"], first["id"])
        st, forced = self.req(
            "/api/incidents",
            dict(
                request_id="ai-2",
                device="d-ai-2",
                description="El semáforo de esta esquina sigue apagado y no funciona",
                lat=-30.90402,
                lng=-55.54902,
                force_new=True,
            ),
            "POST",
        )
        self.assertEqual(st, 201)

    def test_ai_sector_and_community_action(self):
        st, new = self.create(
            "community-1",
            "Hay mucha basura y botellas acumuladas en la plaza del barrio",
            -30.905,
            -55.55,
        )
        self.assertEqual(st, 201)
        self.assertEqual(new["suggested_sector"], "Limpieza urbana")
        self.assertTrue(new["community_suitable"])
        token = self.req(
            "/api/login", {"username": "rivera", "password": "clave-segura"}, "POST"
        )[1]["token"]
        row = next(
            x
            for x in self.req("/api/admin/incidents", token=token)[1]
            if x["id"] == new["id"]
        )
        self.assertEqual(
            self.req(
                f"/api/incidents/{row['id']}",
                {
                    "status": "En revisión",
                    "assignee": row["suggested_sector"],
                    "priority": row["priority"],
                    "note": "Validada",
                    "revision": row["revision"],
                },
                "PATCH",
                token,
            )[0],
            200,
        )
        st, action = self.req(
            f"/api/incidents/{row['id']}/community-action", {}, "POST", token
        )
        self.assertEqual(st, 200)
        self.assertTrue(action["action_id"])
        st, actions = self.req("/api/community-actions")
        self.assertEqual(st, 200)
        a = next(x for x in actions if x["id"] == action["action_id"])
        self.assertEqual(a["public_code"], new["code"])
        st, interest = self.req(
            f"/api/community-actions/{a['id']}/interest", {"device": "vol-1"}, "POST"
        )
        self.assertEqual(st, 200)
        self.assertTrue(interest["added"])
        self.assertEqual(interest["volunteers"], 1)

    def test_login_and_static(self):
        self.assertEqual(
            self.req(
                "/api/login", {"username": "rivera", "password": "incorrecta"}, "POST"
            )[0],
            401,
        )
        for path in ["/", "/app.js", "/style.css", "/manifest.webmanifest"]:
            with urllib.request.urlopen(self.url + path) as r:
                self.assertEqual(r.status, 200)


if __name__ == "__main__":
    unittest.main()
