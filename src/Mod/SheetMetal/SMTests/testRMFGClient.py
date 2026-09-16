# SPDX-License-Identifier: LGPL-2.1-or-later
"""Direct RMFG REST requests, without FreeCAD, credentials, or network access."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest
from unittest.mock import Mock


class TestRMFGClient(unittest.TestCase):
    def setUp(self):
        from SheetMetalRMFGClient import RMFGClient, HTTPResult
        self.HTTPResult = HTTPResult
        self.transport = Mock(return_value=HTTPResult(200, {}, b'{"status":"ready"}'))
        self.client = RMFGClient(lambda: "private-test-token", transport=self.transport)

    def test_catalog_pagination_preserves_the_server_cursor(self):
        self.client.materials(cursor="next page/+", limit=20)
        method, url, headers, body = self.transport.call_args.args
        self.assertEqual(method, "GET")
        self.assertEqual(url, "https://api.rmfg.com/v1/materials?limit=20&cursor=next+page%2F%2B")
        self.assertEqual(headers["Authorization"], "Bearer private-test-token")
        self.assertIsNone(body)

    def test_upload_retries_send_identical_step_bytes_and_operation_key(self):
        data = b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;"
        self.transport.return_value = self.HTTPResult(202, {}, b'{"id":"design_1","status":"processing"}')
        first = self.client.analyze(data, filename="bracket.step", operation_key="analysis-1")
        self.client.analyze(data, filename="bracket.step", operation_key="analysis-1")
        self.assertEqual(self.transport.call_args_list[0], self.transport.call_args_list[1])
        method, url, headers, body = self.transport.call_args.args
        self.assertEqual((method, url), ("POST", "https://api.rmfg.com/v1/analyze"))
        self.assertEqual(headers["Idempotency-Key"], "analysis-1")
        self.assertIn(data, body)
        self.assertIn(b'name="file"; filename="bracket.step"', body)
        self.assertEqual(first["id"], "design_1")
        self.assertEqual(first["status"], "processing")

    def test_polling_uses_durable_ids_without_uploading_again(self):
        self.client.design("design_1")
        self.client.dfm("dfm_1")
        self.client.quote("quote_1")
        self.client.cart("cart_1")
        self.assertEqual([(call.args[0], call.args[1]) for call in self.transport.call_args_list], [
            ("GET", "https://api.rmfg.com/v1/designs/design_1"),
            ("GET", "https://api.rmfg.com/v1/dfm/dfm_1"),
            ("GET", "https://api.rmfg.com/v1/quotes/quote_1"),
            ("GET", "https://api.rmfg.com/v1/carts/cart_1")])

    def test_quote_and_checkout_use_the_same_configuration_and_design_quantities(self):
        configuration = {"parts": [{"part_id": "part_1", "material_id": "material_1"}]}
        items = [{"design_id": "design_1", "quantity": 10, "configuration": configuration}]
        self.client.create_dfm("design_1", configuration, operation_key="dfm-1")
        self.client.create_quote(items, operation_key="quote-1")
        self.transport.return_value = self.HTTPResult(201, {}, b'{"id":"cart_1","cart_url":"https://www.rmfg.com/cart/private-link"}')
        result = self.client.create_checkout(items, operation_key="cart-1")
        dfm, quote, cart = self.transport.call_args_list
        self.assertEqual(json.loads(dfm.args[3]), {"design_id": "design_1", "configuration": configuration})
        self.assertEqual(json.loads(quote.args[3]), {"items": items})
        self.assertEqual(quote.args[3], cart.args[3])
        self.assertEqual(cart.args[1], "https://api.rmfg.com/v1/carts")
        self.assertEqual(result["cart_url"], "https://www.rmfg.com/cart/private-link")
        self.assertFalse(any("/pay" in call.args[1] for call in self.transport.call_args_list))

    def test_rate_limit_and_ambiguous_failure_are_not_silently_retried(self):
        from SheetMetalRMFGClient import RMFGError
        self.transport.return_value = self.HTTPResult(429, {"Retry-After": "12"}, b'{"error":"private-test-token"}')
        with self.assertRaises(RMFGError) as caught:
            self.client.materials()
        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(caught.exception.retry_after, "12")
        self.assertNotIn("private-test-token", str(caught.exception))
        self.assertEqual(self.transport.call_count, 1)
        self.transport.reset_mock()
        self.transport.side_effect = OSError("private-test-token")
        with self.assertRaises(RMFGError) as caught:
            self.client.create_quote([{"design_id": "design_1"}], operation_key="quote-1")
        self.assertNotIn("private-test-token", str(caught.exception))
        self.assertEqual(self.transport.call_count, 1)

    def test_invalid_local_inputs_fail_before_any_request(self):
        cases = [lambda: self.client.materials(limit=True),
                 lambda: self.client.materials(limit=0),
                 lambda: self.client.design("../account"),
                 lambda: self.client.design("https://attacker.example/"),
                 lambda: self.client.analyze(b"STEP", filename="../private.step", operation_key="a"),
                 lambda: self.client.analyze(b"STEP", filename='a"\r\n.step', operation_key="a"),
                 lambda: self.client.analyze(b"", filename="a.step", operation_key="a"),
                 lambda: self.client.create_quote([], operation_key="q"),
                 lambda: self.client.create_quote([{}], operation_key=""),
                 lambda: self.client.create_quote([{"value": float("nan")}], operation_key="q")]
        for operation in cases:
            with self.subTest(operation=operation), self.assertRaises((TypeError, ValueError)):
                operation()
        self.transport.assert_not_called()

    def test_authentication_and_response_failures_do_not_expose_payloads(self):
        from SheetMetalRMFGClient import RMFGClient, RMFGError
        client = RMFGClient(lambda: None, transport=self.transport)
        with self.assertRaises(RMFGError):
            client.materials()
        self.transport.assert_not_called()
        for body in (b"private-test-token", b"[]", b'{"price":NaN}', b'{"price":1e999}'):
            self.transport.return_value = self.HTTPResult(200, {}, body)
            with self.subTest(body=body), self.assertRaises(RMFGError) as caught:
                self.client.materials()
            self.assertNotIn("private-test-token", str(caught.exception))

    def test_default_http_transport_does_not_follow_redirects(self):
        from SheetMetalRMFGClient import _http_request
        paths = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                paths.append(self.path)
                self.send_response(302)
                self.send_header("Location", "/other-origin-target")
                self.end_headers()
            def log_message(self, *args):
                pass
        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            worker = threading.Thread(target=server.serve_forever)
            worker.start()
            try:
                result = _http_request("GET", f"http://127.0.0.1:{server.server_port}/request",
                                       {"Authorization": "Bearer private-test-token"}, None)
            finally:
                server.shutdown()
                worker.join()
        self.assertEqual(result.status, 302)
        self.assertEqual(paths, ["/request"])


if __name__ == "__main__":
    unittest.main()
