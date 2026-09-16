"""Detalles de actividades comunitarias, edición y privacidad."""

import unittest
import test_security as support
import server


class CommunityDetailsTest(unittest.TestCase):
    setUp = support.SecurityTest.setUp
    login = support.SecurityTest.login
    report = support.SecurityTest.report

    def test_details_edit_revision_and_public_visibility(self):
        created = self.report(address="Sarandí y Agraciada")
        self.assertEqual(created.status_code, 201)
        ident = created.json["id"]
        headers = {"Authorization": "Bearer " + self.login()}
        with server.connection() as con:
            con.execute(
                "UPDATE incidents SET status='En revisión' WHERE id=?", (ident,)
            )
        payload = dict(
            title="Limpieza de la plaza",
            activity_details="Recoger papeles de los senderos de la plaza.",
            meeting_point="Entrada principal",
            schedule="26/09/2026, de 09:00 a 11:00",
            organizer="Comisión vecinal",
            materials="Llevar guantes y agua.",
        )
        path = f"/api/incidents/{ident}/community-action"
        response = self.client.post(path, json=payload, headers=headers)
        self.assertEqual(response.status_code, 200)
        action_id = response.json["action_id"]
        action = self.client.get("/api/community-actions").json[0]
        for key, value in payload.items():
            self.assertEqual(action[key], value)
        self.assertEqual(action["address"], "Sarandí y Agraciada")
        self.assertEqual(action["incident_description"], "Hay basura en la plaza")
        self.assertEqual(action["lat"], -30.905)
        self.client.post(
            f"/api/community-actions/{action_id}/interest", json={"device": "volunteer"}
        )
        payload.update(revision=action["revision"], meeting_point="Entrada norte")
        self.assertEqual(
            self.client.post(path, json=payload, headers=headers).status_code, 200
        )
        self.assertEqual(
            self.client.post(path, json=payload, headers=headers).status_code, 409
        )
        updated = self.client.get("/api/community-actions").json[0]
        self.assertEqual(updated["volunteers"], 1)
        self.assertEqual(updated["meeting_point"], "Entrada norte")
        wrong = {"Authorization": "Bearer " + self.login("livramento")}
        payload["revision"] = updated["revision"]
        self.assertEqual(
            self.client.post(path, json=payload, headers=wrong).status_code, 403
        )
        with server.connection() as con:
            con.execute("UPDATE incidents SET status='Rechazado' WHERE id=?", (ident,))
        self.assertEqual(self.client.get("/api/community-actions").json, [])
        self.assertEqual(
            self.client.post(
                f"/api/community-actions/{action_id}/interest", json={"device": "other"}
            ).status_code,
            404,
        )

    def test_activity_requires_tasks_when_details_are_submitted(self):
        created = self.report()
        ident = created.json["id"]
        with server.connection() as con:
            con.execute(
                "UPDATE incidents SET status='En revisión' WHERE id=?", (ident,)
            )
        headers = {"Authorization": "Bearer " + self.login()}
        response = self.client.post(
            f"/api/incidents/{ident}/community-action",
            json={"title": "Jornada"},
            headers=headers,
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
