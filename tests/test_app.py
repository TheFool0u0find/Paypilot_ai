import unittest
from unittest.mock import patch

import app


class SmartCartApiTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        self.credentials = patch.multiple(
            app,
            PAYPAL_CLIENT_ID="client-id",
            PAYPAL_CLIENT_SECRET="client-secret",
        )
        self.credentials.start()
        self.addCleanup(self.credentials.stop)

    def cart(self):
        return self.client.get("/api/cart").get_json()

    def add_product(self, product_id, quantity=1):
        return self.client.post(
            "/api/cart",
            json={
                "action": "add",
                "product_id": product_id,
                "quantity": quantity,
            },
        )

    def test_ai_requirement_analysis_uses_openrouter_chat_completion(self):
        response_message = type("Message", (), {
            "content": '{"category":"keyboard","budget":{"amount":50,"currency":"USD"},"intended_use":"programming","required_features":["mechanical"],"preferred_features":[],"lower_priority_features":[],"expected_useful_lifetime":null,"clarifying_questions":[]}'
        })()
        fake_response = type("Response", (), {
            "choices": [type("Choice", (), {"message": response_message})()]
        })()
        fake_client = type("Client", (), {
            "chat": type("Chat", (), {
                "completions": type("Completions", (), {
                    "create": lambda *args, **kwargs: fake_response
                })()
            })()
        })()
        with patch.object(app, "OPENROUTER_API_KEY", "test-key"), patch.object(
            app, "openrouter_client", return_value=fake_client
        ):
            response = self.client.post(
                "/api/ai/analyze-requirements",
                json={"request": "keyboard under $50 for programming"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ai_used"])
        self.assertEqual(
            response.get_json()["requirements"]["category"], "keyboard"
        )

    def test_ai_fallback_is_labelled_when_openrouter_is_missing(self):
        with patch.object(app, "openrouter_client", return_value=None):
            response = self.client.post(
                "/api/ai/analyze-requirements",
                json={"request": "something fun under 100 rupees"},
            )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertFalse(data["ai_used"])
        self.assertEqual(data["requirements"]["budget"]["currency"], "INR")

    def test_ai_invalid_openrouter_output_uses_fallback(self):
        response_message = type("Message", (), {"content": "not json"})()
        fake_response = type("Response", (), {
            "choices": [type("Choice", (), {"message": response_message})()]
        })()
        fake_client = type("Client", (), {
            "chat": type("Chat", (), {
                "completions": type("Completions", (), {
                    "create": lambda *args, **kwargs: fake_response
                })()
            })()
        })()
        with patch.object(app, "openrouter_client", return_value=fake_client):
            response = self.client.post(
                "/api/ai/analyze-requirements",
                json={"request": "keyboard under $50"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["ai_used"])

    def test_catalogue_contains_server_controlled_products(self):
        response = self.client.get("/api/products")
        self.assertEqual(response.status_code, 200)
        products = response.get_json()["products"]
        self.assertGreaterEqual(len(products), 8)
        self.assertTrue(all(product["id"] in app.PRODUCTS for product in products))
        self.assertTrue(all(product["price"].count(".") == 1 for product in products))

    def test_recommendations_and_purchase_promise(self):
        response = self.client.post(
            "/api/recommendations",
            json={"request": "I need a keyboard for programming under $80"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["requirements"]["budget"]["amount"], "80.00")
        self.assertEqual(data["requirements"]["primary_use"], "programming")
        self.assertTrue(data["products"])

        promise = self.client.post(
            "/api/promises",
            json={
                "original_request": "I need a keyboard for programming under $80",
                "product_id": "keyboard",
            },
        )
        self.assertEqual(promise.status_code, 201)
        promise_data = promise.get_json()
        self.assertEqual(promise_data["status"], "draft")

        approved = self.client.post(
            f"/api/promises/{promise_data['id']}/approve"
        )
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(self.cart()["items"][0]["product"]["id"], "keyboard")

    def test_promise_rejects_unknown_product_and_guardian_is_simulated(self):
        response = self.client.post(
            "/api/promises",
            json={"original_request": "wireless earbuds under $50", "product_id": "nope"},
        )
        self.assertEqual(response.status_code, 400)

        promise = self.client.post(
            "/api/promises",
            json={"original_request": "wireless earbuds under $50", "product_id": "echo-buds"},
        ).get_json()
        alert = self.client.post(f"/api/guardian/{promise['id']}/check")
        self.assertEqual(alert.status_code, 200)
        self.assertTrue(alert.get_json()["simulated"])

    def test_cart_add_update_remove_and_invalid_quantity(self):
        added = self.add_product("keyboard", 2)
        self.assertEqual(added.status_code, 200)
        self.assertEqual(added.get_json()["item_count"], 2)

        updated = self.client.post(
            "/api/cart",
            json={"action": "update", "product_id": "keyboard", "quantity": 3},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["items"][0]["quantity"], 3)

        invalid = self.client.post(
            "/api/cart",
            json={"action": "update", "product_id": "keyboard", "quantity": 0},
        )
        self.assertEqual(invalid.status_code, 400)

        unknown = self.add_product("not-a-product")
        self.assertEqual(unknown.status_code, 400)

        removed = self.client.post(
            "/api/cart",
            json={"action": "remove", "product_id": "keyboard"},
        )
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(removed.get_json()["items"], [])

    def test_empty_cart_checkout_is_rejected(self):
        with patch.object(app, "paypal_api") as paypal_api:
            response = self.client.post("/api/create-order")
        self.assertEqual(response.status_code, 400)
        paypal_api.assert_not_called()

    def test_create_order_uses_server_total_not_client_price(self):
        self.add_product("travel-mug", 2)
        self.add_product("notebook", 1)

        with patch.object(
            app, "paypal_api", return_value={"id": "ORDER-123"}
        ) as paypal_api:
            response = self.client.post("/api/create-order")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["total"], "45.50")
        payload = paypal_api.call_args.args[2]
        amount = payload["purchase_units"][0]["amount"]
        self.assertEqual(amount["value"], "45.50")
        self.assertNotIn("price", payload)

    def test_capture_requires_recognized_order(self):
        response = self.client.post(
            "/api/capture-order", json={"orderID": "unrecognized"}
        )
        self.assertEqual(response.status_code, 400)

    def test_successful_capture_clears_cart(self):
        self.add_product("notebook", 2)
        with patch.object(app, "paypal_api", return_value={"id": "ORDER-123"}):
            self.assertEqual(self.client.post("/api/create-order").status_code, 200)

        capture_response = {
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "payments": {
                        "captures": [
                            {
                                "status": "COMPLETED",
                                "amount": {
                                    "currency_code": "USD",
                                    "value": "19.00",
                                },
                            }
                        ]
                    }
                }
            ],
        }
        with patch.object(app, "paypal_api", return_value=capture_response):
            response = self.client.post(
                "/api/capture-order", json={"orderID": "ORDER-123"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "COMPLETED")
        self.assertEqual(self.cart()["items"], [])

    def test_capture_rejects_wrong_amount_or_currency(self):
        self.add_product("notebook")
        with patch.object(app, "paypal_api", return_value={"id": "ORDER-456"}):
            self.client.post("/api/create-order")

        wrong_capture = {
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "payments": {
                        "captures": [
                            {
                                "status": "COMPLETED",
                                "amount": {
                                    "currency_code": "EUR",
                                    "value": "9.50",
                                },
                            }
                        ]
                    }
                }
            ],
        }
        with patch.object(app, "paypal_api", return_value=wrong_capture):
            response = self.client.post(
                "/api/capture-order", json={"orderID": "ORDER-456"}
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("verified", response.get_json()["error"])

    def test_paypal_api_errors_are_handled(self):
        self.add_product("desk-lamp")
        with patch.object(
            app, "paypal_api", side_effect=app.requests.Timeout
        ):
            response = self.client.post("/api/create-order")
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("access token", response.get_json()["error"].lower())


if __name__ == "__main__":
    unittest.main()
