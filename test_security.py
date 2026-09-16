"""Regresiones de seguridad, errores, migraciones y límites sin servicios externos."""

import base64
import io
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from PIL import Image

os.environ["DATABASE_URL"] = ""  # Nunca ejecutar esta suite contra una base externa.
import server


class SecurityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {"DEMO_MODE": "0", "OPENAI_API_KEY": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.db = patch.object(server, "DB", Path(self.tmp.name) / "test.sqlite3")
        self.db.start()
        self.addCleanup(self.db.stop)
        with patch.object(server.threading.Thread, "start"):
            self.app = server.create_app()
        self.client = self.app.test_client()
        salt, digest = server.password_hash("test-password-123")
        with server.connection() as con:
            for username, city in [
                ("rivera", "Rivera"),
                ("livramento", "Santana do Livramento"),
            ]:
                con.execute(
                    "INSERT INTO admin_users(username,password_salt,password_hash,city,created_at) VALUES (?,?,?,?,?)",
                    (username, salt, digest, city, server.now()),
                )

    def login(self, username="rivera"):
        response = self.client.post(
            "/api/login", json={"username": username, "password": "test-password-123"}
        )
        self.assertEqual(response.status_code, 200)
        return response.json["token"]

    def report(self, **extra):
        body = dict(
            description="Hay basura en la plaza",
            lat=-30.905,
            lng=-55.55,
            device="test",
            request_id="test",
        )
        body.update(extra)
        return self.client.post("/api/incidents", json=body)

    def test_login_limit_case_insensitive_persistent_and_retry_header(self):
        for _ in range(5):
            self.assertEqual(
                self.client.post(
                    "/api/login", json={"username": "RIVERA", "password": "bad"}
                ).status_code,
                401,
            )
        response = self.client.post(
            "/api/login", json={"username": "rivera", "password": "test-password-123"}
        )
        self.assertEqual(response.status_code, 429)
        self.assertGreater(int(response.headers["Retry-After"]), 0)
        with server.connection() as con:
            con.execute("UPDATE rate_limits SET expires=0")
        self.login()

    def test_ip_limit_cannot_be_bypassed_with_forwarded_header(self):
        for i in range(30):
            response = self.client.post(
                "/api/login",
                json={"username": f"unknown{i}", "password": "wrong"},
                headers={"X-Forwarded-For": f"10.0.0.{i}"},
            )
            self.assertEqual(response.status_code, 401)
        self.assertEqual(
            self.client.post(
                "/api/login", json={"username": "other", "password": "wrong"}
            ).status_code,
            429,
        )

    def test_sessions_hashed_expire_logout_and_disabled_user(self):
        token = self.login()
        headers = {"Authorization": "Bearer " + token}
        with server.connection() as con:
            stored = con.execute(
                "SELECT token,expires_at FROM admin_sessions"
            ).fetchone()
            self.assertNotEqual(stored["token"], token)
            self.assertTrue(stored["expires_at"])
            con.execute(
                "UPDATE admin_sessions SET expires_at=?",
                ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),),
            )
        self.assertEqual(
            self.client.get("/api/session", headers=headers).status_code, 401
        )
        server.cleanup_expired()
        with server.connection() as con:
            self.assertEqual(
                con.execute("SELECT count(*) FROM admin_sessions").fetchone()[0], 0
            )
        token = self.login()
        headers = {"Authorization": "Bearer " + token}
        self.client.post("/api/logout", json={}, headers=headers)
        self.assertEqual(
            self.client.get("/api/session", headers=headers).status_code, 401
        )
        token = self.login()
        headers = {"Authorization": "Bearer " + token}
        with server.connection() as con:
            con.execute("UPDATE admin_users SET active=0 WHERE username='rivera'")
        self.assertEqual(
            self.client.get("/api/admin/incidents", headers=headers).status_code, 401
        )

    def test_malformed_json_invalid_shape_large_body_and_corrupt_photos(self):
        for payload in (b"{", b"[]", b"null", b"\xff"):
            self.assertEqual(
                self.client.post(
                    "/api/incidents", data=payload, content_type="application/json"
                ).status_code,
                400,
            )
        self.assertEqual(
            self.client.post(
                "/api/incidents",
                data=b"x" * (server.MAX_BODY + 1),
                content_type="application/json",
            ).status_code,
            413,
        )
        for photo in (
            "no-image",
            "data:image/png;base64,!!!",
            "data:image/png;base64,YmFk",
        ):
            self.assertEqual(self.report(photo=photo).status_code, 400)
        with self.assertRaises(ValueError):
            server.photo_bytes(
                "data:image/jpeg;base64,"
                + base64.b64encode(b"x" * (3 * 1024 * 1024 + 1)).decode()
            )
        with patch.object(server.Image, "MAX_IMAGE_PIXELS", 10):
            image = io.BytesIO()
            Image.new("RGB", (10, 10)).save(image, "PNG")
            self.assertEqual(
                self.report(
                    photo="data:image/png;base64,"
                    + base64.b64encode(image.getvalue()).decode()
                ).status_code,
                400,
            )

    def test_incident_rate_limit_precedes_analysis(self):
        with patch.dict(os.environ, {"INCIDENT_RATE_LIMIT": "2"}):
            for i in range(2):
                self.assertEqual(self.report(request_id=str(i)).status_code, 201)
            with patch.object(server, "openai_analysis") as analysis:
                self.assertEqual(
                    self.report(request_id="3", device="new-device").status_code, 429
                )
                analysis.assert_not_called()

    def test_revision_cross_city_invalid_tokens_and_private_photo(self):
        img = io.BytesIO()
        Image.new("RGB", (4, 4)).save(img, "PNG")
        created = self.report(
            photo="data:image/png;base64," + base64.b64encode(img.getvalue()).decode()
        )
        self.assertEqual(created.status_code, 201)
        ident = created.json["id"]
        path = f"/api/incidents/{ident}"
        body = {
            "revision": 1,
            "status": "En revisión",
            "assignee": "Sin asignar",
            "note": "Validada",
        }
        self.assertEqual(self.client.get(path + "/photo").status_code, 404)
        self.assertEqual(
            self.client.patch(
                path, json=body, headers={"Authorization": "Bearer invalid"}
            ).status_code,
            401,
        )
        wrong = {"Authorization": "Bearer " + self.login("livramento")}
        self.assertEqual(
            self.client.patch(path, json=body, headers=wrong).status_code, 403
        )
        self.assertEqual(
            self.client.get(path + "/photo", headers=wrong).status_code, 404
        )
        right = {"Authorization": "Bearer " + self.login()}
        self.assertEqual(
            self.client.get(path + "/photo", headers=right).status_code, 200
        )
        self.assertEqual(
            self.client.patch(path, json=body, headers=right).status_code, 200
        )
        self.assertEqual(
            self.client.patch(path, json=body, headers=right).status_code, 409
        )
        self.assertEqual(self.client.get(path + "/photo").status_code, 200)

    def test_pagination_filter_and_complete_report(self):
        for i in range(3):
            self.report(request_id=str(i))
        headers = {"Authorization": "Bearer " + self.login()}
        response = self.client.get(
            "/api/admin/incidents?page=1&limit=2", headers=headers
        )
        self.assertEqual(response.json["total"], 3)
        self.assertEqual(len(response.json["items"]), 2)
        next_page = self.client.get(
            "/api/admin/incidents?page=2&limit=2", headers=headers
        ).json
        self.assertEqual(len(next_page["items"]), 1)
        self.assertFalse(
            {x["id"] for x in response.json["items"]}
            & {x["id"] for x in next_page["items"]}
        )
        self.assertEqual(
            self.client.get(
                "/api/admin/incidents?page=1&city=Santana+do+Livramento",
                headers=headers,
            ).json["total"],
            0,
        )
        self.assertEqual(
            self.client.get(
                "/api/admin/incidents?page=bad", headers=headers
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.get("/api/admin/report", headers=headers).json["length"], 3
        )
        self.assertEqual(
            self.client.get("/api/admin/report?from=bad", headers=headers).status_code,
            400,
        )

    def test_headers_health_and_demo_disabled(self):
        for path in ("/", "/app.js", "/api/health", "/api/missing"):
            response = self.client.get(path)
            self.assertIn(
                "script-src 'self'", response.headers["Content-Security-Policy"]
            )
            response.close()
        with patch.object(
            server, "connection", side_effect=RuntimeError("unavailable")
        ):
            self.assertEqual(self.client.get("/api/health").status_code, 503)
        self.assertEqual(
            self.client.post(
                "/api/demo",
                json={},
                headers={"Authorization": "Bearer " + self.login()},
            ).status_code,
            403,
        )

    def test_limits_atomic_under_concurrent_requests(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(
                pool.map(lambda _: server.rate_limit("test", "ip", 5, 900), range(20))
            )
        self.assertEqual(results.count(0), 5)

    def test_multiple_photos_disk_and_access_control(self):
        image = io.BytesIO()
        Image.new("RGB", (8, 8)).save(image, "PNG")
        photo = "data:image/png;base64," + base64.b64encode(image.getvalue()).decode()
        with patch.dict(os.environ, {"PHOTOS_DIR": self.tmp.name + "/photos"}):
            response = self.report(photos=[photo, photo, photo])
            self.assertEqual(response.status_code, 201)
            ident = response.json["id"]
            headers = {"Authorization": "Bearer " + self.login()}
            rows = self.client.get("/api/admin/incidents", headers=headers).json
            self.assertEqual(len(rows[0]["photo_urls"]), 3)
            for path in rows[0]["photo_urls"]:
                self.assertEqual(self.client.get(path).status_code, 404)
                self.assertEqual(
                    self.client.get(path, headers=headers).status_code, 200
                )
            with server.connection() as con:
                row = con.execute(
                    "SELECT photo,photo_path FROM incidents WHERE id=?", (ident,)
                ).fetchone()
                self.assertIsNone(row["photo"])
                self.assertTrue(row["photo_path"])
            self.assertEqual(
                self.report(request_id="too-many", photos=[photo] * 4).status_code, 400
            )

    def test_bulk_changes_are_atomic_and_city_scoped(self):
        one = self.report(request_id="one").json["id"]
        two = self.report(request_id="two").json["id"]
        other = self.report(request_id="other", lat=-30.886, lng=-55.53).json["id"]
        headers = {"Authorization": "Bearer " + self.login()}
        body = {
            "items": [{"id": one, "revision": 1}, {"id": other, "revision": 1}],
            "status": "En revisión",
            "note": "Aprobación",
        }
        self.assertEqual(
            self.client.post(
                "/api/admin/incidents/bulk", json=body, headers=headers
            ).status_code,
            403,
        )
        with server.connection() as con:
            self.assertEqual(
                con.execute(
                    "SELECT revision FROM incidents WHERE id=?", (one,)
                ).fetchone()[0],
                1,
            )
        body["items"] = [{"id": one, "revision": 1}, {"id": two, "revision": 2}]
        self.assertEqual(
            self.client.post(
                "/api/admin/incidents/bulk", json=body, headers=headers
            ).status_code,
            409,
        )
        with server.connection() as con:
            self.assertEqual(
                con.execute(
                    "SELECT revision FROM incidents WHERE id=?", (one,)
                ).fetchone()[0],
                1,
            )
        body["items"][1]["revision"] = 1
        self.assertEqual(
            self.client.post(
                "/api/admin/incidents/bulk", json=body, headers=headers
            ).json["updated"],
            2,
        )

    def test_only_owner_manages_accounts_and_reset_revokes_sessions(self):
        ordinary = {"Authorization": "Bearer " + self.login()}
        self.assertEqual(
            self.client.get("/api/owner/users", headers=ordinary).status_code, 403
        )
        body = {
            "action": "create",
            "username": "new-user",
            "city": "Rivera",
            "password": "new-password-123",
        }
        self.assertEqual(
            self.client.post(
                "/api/owner/users", headers=ordinary, json=body
            ).status_code,
            403,
        )
        with server.connection() as con:
            con.execute("UPDATE admin_users SET is_owner=1 WHERE username='rivera'")
        self.assertEqual(
            self.client.post(
                "/api/owner/users", headers=ordinary, json=body
            ).status_code,
            200,
        )
        logged = self.client.post(
            "/api/login", json={"username": "new-user", "password": "new-password-123"}
        ).json
        child = {"Authorization": "Bearer " + logged["token"]}
        body.update(action="reset", password="another-password-123")
        self.assertEqual(
            self.client.post(
                "/api/owner/users", headers=ordinary, json=body
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get("/api/session", headers=child).status_code, 401
        )
        body.update(action="disable", username="rivera")
        self.assertEqual(
            self.client.post(
                "/api/owner/users", headers=ordinary, json=body
            ).status_code,
            403,
        )

    def test_old_session_migration_and_shared_password_disabled(self):
        with server.connection() as con:
            con.execute("DROP TABLE admin_sessions")
            con.execute(
                "CREATE TABLE admin_sessions(token TEXT PRIMARY KEY, username TEXT, city TEXT, created_at TEXT)"
            )
            con.execute(
                "INSERT INTO admin_sessions VALUES ('old','rivera','Rivera',?)",
                (server.now(),),
            )
            salt, digest = server.password_hash("CiudadVisible2026!")
            con.execute(
                "UPDATE admin_users SET password_salt=?,password_hash=? WHERE username='rivera'",
                (salt, digest),
            )
        server.init()
        with server.connection() as con:
            self.assertEqual(
                con.execute("SELECT count(*) FROM admin_sessions").fetchone()[0], 0
            )
            self.assertEqual(
                con.execute(
                    "SELECT active FROM admin_users WHERE username='rivera'"
                ).fetchone()[0],
                0,
            )


class PureTest(unittest.TestCase):
    def test_distance_category_priority_and_community(self):
        self.assertEqual(server.haversine(0, 0, 0, 0), 0)
        self.assertAlmostEqual(server.haversine(0, 0, 0, 1), 111195, delta=5)
        self.assertEqual(server.infer_category("Hay un bache"), "Baches")
        self.assertEqual(server.infer_category("Há lixo na praça"), "Basura")
        self.assertEqual(server.fallback_priority("cable caído")[0], "Urgente")
        self.assertFalse(server.community_suggestion("cable caído", "Otros")[0])
        self.assertTrue(server.community_suggestion("basura en plaza", "Basura")[0])

    def test_semantic_fallback_integration(self):
        candidates = [
            {
                "id": 1,
                "description": "Residuos acumulados",
                "title": "Residuos",
                "category": "Basura",
                "distance_m": 5,
            }
        ]
        with patch.object(server, "similarities", return_value=[0.97]):
            result = server.fallback_analysis("Hay basura", candidates, "Rivera")
        self.assertEqual(result["duplicate_id"], 1)
        self.assertGreaterEqual(result["duplicate_confidence"], 0.68)

    def test_public_demo_requires_opt_in_and_custom_password(self):
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "production",
                "DEMO_MODE": "1",
                "ALLOW_PUBLIC_DEMO": "1",
                "DEMO_ADMIN_PASSWORD": "a-unique-test-password",
            },
        ):
            server.validate_configuration()
            for password in ("", "short", "CiudadVisible2026!"):
                with patch.dict(os.environ, {"DEMO_ADMIN_PASSWORD": password}):
                    with self.assertRaises(RuntimeError):
                        server.validate_configuration()

    def test_production_rejects_demo(self):
        with patch.dict(os.environ, {"APP_ENV": "production", "DEMO_MODE": "1"}):
            with self.assertRaises(RuntimeError):
                server.validate_configuration()

    def test_circuit_breaker_skips_network_and_recovers(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}), patch.object(
            server, "_ai_state", {"failures": 0, "until": 0, "probe": False}
        ), patch.object(
            server, "_openai_analysis", return_value={"source": "fallback"}
        ) as call:
            for _ in range(5):
                server.openai_analysis("bache", [], "Rivera")
            self.assertEqual(call.call_count, 3)
            server._ai_state["until"] = 0
            call.return_value = {
                "source": "openai",
                "_usage": {"input_tokens": 10, "output_tokens": 5},
            }
            server.openai_analysis("bache", [], "Rivera")
            self.assertEqual(call.call_count, 4)
            self.assertEqual(server._ai_state["failures"], 0)


if __name__ == "__main__":
    unittest.main()
