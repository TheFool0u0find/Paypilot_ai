import logging
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, session

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")

PAYPAL_BASE_URL = os.getenv(
    "PAYPAL_BASE_URL",
    "https://api-m.sandbox.paypal.com",
).rstrip("/")
PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/free").strip()
DATABASE_PATH = os.getenv(
    "SMARTCART_DATABASE",
    os.path.join(os.path.dirname(__file__), "smartcart.db"),
)

CURRENCY = "USD"
MAX_QUANTITY = 20
MAX_CART_ITEMS = 100


class PayPalAPIError(requests.RequestException):
    def __init__(self, status_code, name, message, debug_id=None):
        super().__init__(message)
        self.status_code = status_code
        self.name = name
        self.debug_id = debug_id


PRODUCTS = {
    "echo-buds": {
        "id": "echo-buds",
        "name": "EchoBuds Wireless Earbuds",
        "description": "Compact wireless earbuds with a charging case.",
        "category": "Electronics",
        "price": "29.99",
        "image": "https://images.unsplash.com/photo-1590658268037-6bf12165a8df?auto=format&fit=crop&w=700&q=80",
        "available": True,
        "specs": {"battery": "24 hours", "connectivity": "Bluetooth"},
    },
    "desk-lamp": {
        "id": "desk-lamp",
        "name": "Focus Desk Lamp",
        "description": "A warm, adjustable LED lamp for focused work.",
        "category": "Everyday Essentials",
        "price": "24.50",
        "image": "https://images.unsplash.com/photo-1507473885765-e6ed057f782c?auto=format&fit=crop&w=700&q=80",
        "available": True,
        "specs": {"type": "LED", "adjustable": "Yes"},
    },
    "travel-mug": {
        "id": "travel-mug",
        "name": "Insulated Travel Mug",
        "description": "Leak-resistant stainless steel mug for daily commutes.",
        "category": "Everyday Essentials",
        "price": "18.00",
        "image": "https://images.unsplash.com/photo-1517256064527-09c73fc73e38?auto=format&fit=crop&w=700&q=80",
        "available": True,
        "specs": {"capacity": "12 oz", "material": "Stainless steel"},
    },
    "keyboard": {
        "id": "keyboard",
        "name": "Compact Mechanical Keyboard",
        "description": "A tactile, space-saving keyboard for your desk.",
        "category": "Electronics",
        "price": "64.99",
        "image": "https://images.unsplash.com/photo-1587829741301-dc798b83add3?auto=format&fit=crop&w=700&q=80",
        "available": True,
        "specs": {"layout": "Compact", "switches": "Mechanical"},
    },
    "backpack": {
        "id": "backpack",
        "name": "Everyday Tech Backpack",
        "description": "Comfortable backpack with room for a laptop and essentials.",
        "category": "Accessories",
        "price": "49.95",
        "image": "https://images.unsplash.com/photo-1553062407-98eeb64c6a62?auto=format&fit=crop&w=700&q=80",
        "available": True,
        "specs": {"capacity": "20 L", "laptop": "Up to 15 inch"},
    },
    "power-bank": {
        "id": "power-bank",
        "name": "Pocket Power Bank",
        "description": "Portable 10,000 mAh power for phones and small devices.",
        "category": "Electronics",
        "price": "22.75",
        "image": "https://images.unsplash.com/photo-1609592424807-7b5f7c9f8c5c?auto=format&fit=crop&w=700&q=80",
        "available": True,
        "specs": {"capacity": "10,000 mAh", "ports": "USB-C and USB-A"},
    },
    "notebook": {
        "id": "notebook",
        "name": "Recycled Paper Notebook",
        "description": "A durable lined notebook for plans, ideas, and lists.",
        "category": "Everyday Essentials",
        "price": "9.50",
        "image": "https://images.unsplash.com/photo-1517842645767-c639042777db?auto=format&fit=crop&w=700&q=80",
        "available": True,
        "specs": {"pages": "160", "paper": "Recycled"},
    },
    "phone-stand": {
        "id": "phone-stand",
        "name": "Aluminium Phone Stand",
        "description": "A sturdy stand for hands-free calls and viewing.",
        "category": "Accessories",
        "price": "16.25",
        "image": "https://images.unsplash.com/photo-1586953208448-b95a79798f07?auto=format&fit=crop&w=700&q=80",
        "available": True,
        "specs": {"material": "Aluminium", "angle": "Adjustable"},
    },
    "cable-kit": {
        "id": "cable-kit",
        "name": "Multi-Cable Charging Kit",
        "description": "A handy set of charging cables for everyday devices.",
        "category": "Accessories",
        "price": "14.99",
        "image": "https://images.unsplash.com/photo-1558618666-fcd25c85cd64?auto=format&fit=crop&w=700&q=80",
        "available": True,
        "specs": {"connectors": "USB-C, Lightning, Micro-USB"},
    },
}


def credentials_ready():
    return bool(
        app.secret_key
        and PAYPAL_CLIENT_ID
        and PAYPAL_CLIENT_SECRET
        and PAYPAL_BASE_URL == "https://api-m.sandbox.paypal.com"
    )


def utc_now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def database():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_database():
    with database() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS promises (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_request TEXT NOT NULL,
                requirements_json TEXT NOT NULL,
                product_id TEXT NOT NULL,
                status TEXT NOT NULL,
                payment_status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                approved_at TEXT,
                paid_at TEXT
            );
            CREATE TABLE IF NOT EXISTS guardian_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                promise_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                message TEXT NOT NULL,
                source TEXT NOT NULL,
                verified INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (promise_id) REFERENCES promises(id)
            );
            """
        )


init_database()


def parse_requirements(text):
    normalized = text.strip()
    if not normalized or len(normalized) > 500:
        raise ValueError("Tell us what you need in 1-500 characters.")

    budget_match = re.search(
        r"(?:₹|rs\.?|inr|rupees?|रुपये|\$|usd)?\s*([\d,]+(?:\.\d{1,2})?)\s*(?:k|thousand)?",
        normalized,
        re.IGNORECASE,
    )
    budget = None
    if budget_match:
        amount = Decimal(budget_match.group(1).replace(",", ""))
        if re.search(r"\bk\b|\bthousand\b", budget_match.group(0), re.I):
            amount *= 1000
        budget = {
            "amount": str(amount.quantize(Decimal("0.01"))),
            "currency": "INR" if (
                "₹" in normalized
                or "रुप" in normalized
                or re.search(r"\binr\b|\brs\.?|\brupees?\b", normalized, re.I)
            ) else "USD",
        }

    lower = normalized.lower()
    use = (
        "programming" if any(word in lower for word in ("code", "coding", "programming", "developer"))
        else "work" if any(word in lower for word in ("work", "office", "study"))
        else "everyday use"
    )
    lifetime_match = re.search(r"(\d+)\s*(?:years?|yrs?)", lower)
    requirements = {
        "budget": budget,
        "primary_use": use,
        "useful_lifetime_goal_years": int(lifetime_match.group(1)) if lifetime_match else None,
        "must_have": [],
        "important": [],
        "unknown": ["Warranty and repairability are not verified in the demo catalogue."],
        "source": "Rule-based fallback; no language model is configured.",
    }
    if any(word in lower for word in ("laptop", "computer", "coding", "programming")):
        requirements["must_have"] = ["Programming suitability"]
        requirements["important"] = ["RAM", "CPU", "SSD storage", "Warranty", "Repairability"]
    elif "wireless" in lower or "earbuds" in lower:
        requirements["important"] = ["Wireless connectivity", "Battery life"]
    return requirements


def recommendation_for(requirements):
    scored = []
    budget = requirements.get("budget")
    budget_amount = Decimal(budget["amount"]) if budget and budget["currency"] == "USD" else None
    for product in PRODUCTS.values():
        score = 0
        reasons = []
        if budget_amount is None or product_price(product) <= budget_amount:
            score += 50
            reasons.append("Within the server-controlled demo budget." if budget_amount else "No supported USD budget was supplied.")
        if requirements["primary_use"] == "everyday use":
            score += 20
            reasons.append("Suitable for everyday essentials.")
        elif requirements["primary_use"] == "work" and product["category"] in {"Electronics", "Accessories"}:
            score += 25
            reasons.append("Relevant to work and desk use.")
        elif requirements["primary_use"] == "programming" and product["id"] == "keyboard":
            score += 35
            reasons.append("A keyboard is directly relevant to programming.")
        else:
            reasons.append("Primary-use match is not verified by demo data.")
        scored.append({**product, "price": format_money(product_price(product)), "match_score": score, "reasons": reasons})
    return sorted(scored, key=lambda product: product["match_score"], reverse=True)[:3]


def match_details(product, requirements):
    budget = requirements.get("budget")
    budget_amount = (
        Decimal(budget["amount"])
        if budget and budget.get("currency") == CURRENCY
        else None
    )
    score = 0
    reasons = []
    risks = []
    if budget_amount is None:
        reasons.append("No supported USD budget was supplied.")
    elif product_price(product) <= budget_amount:
        score += 50
        reasons.append("The server-controlled price is within the stated USD budget.")
    else:
        risks.append("The server-controlled price is above the stated USD budget.")
    if requirements.get("primary_use") == "programming" and product["id"] == "keyboard":
        score += 35
        reasons.append("The product type is directly relevant to programming.")
    elif requirements.get("primary_use") == "work" and product["category"] in {"Electronics", "Accessories"}:
        score += 25
        reasons.append("The product category is relevant to work or desk use.")
    else:
        risks.append("The requested use is not verified by the recorded specifications.")
    risks.append("Warranty, repairability, ratings, and live retailer information are unknown.")
    return {
        "match_score": score,
        "score_method": "50 points for budget fit and up to 35 points for a verified use-case rule.",
        "reasons": reasons,
        "risk_flags": risks,
        "unknown": ["Warranty", "Repairability", "Live price", "Retailer availability"],
        "budget_comparison": (
            f"${format_money(product_price(product))} of "
            f"${format_money(budget_amount)} USD"
            if budget_amount is not None
            else "A supported USD budget was not provided."
        ),
    }


def openrouter_client():
    if not OPENROUTER_API_KEY or not OPENROUTER_MODEL or OpenAI is None:
        return None
    return OpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
        timeout=20.0,
        max_retries=0,
    )


def ai_json(task, payload, schema):
    client = openrouter_client()
    if client is None:
        raise RuntimeError(
            "OpenRouter is not configured. Using the labelled rule-based fallback."
        )
    try:
        response = client.chat.completions.create(
            model=OPENROUTER_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are the PayPilot AI shopping assistant. "
                        "Return only valid JSON matching the supplied schema. "
                        "Use null or empty arrays when information is unknown. "
                        "Never invent product facts, prices, ratings, availability, or guarantees."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps({"task": task, "data": payload}),
                },
            ],
        )
    except Exception as error:
        app.logger.warning(
            "OpenRouter request failed: %s",
            type(error).__name__,
        )
        raise RuntimeError(
            "OpenRouter is unavailable. Using the labelled rule-based fallback."
        ) from error
    try:
        content = response.choices[0].message.content
        if not isinstance(content, str) or not content.strip():
            raise ValueError("empty response")
        content = content.strip()
        if content.startswith("```") and content.endswith("```"):
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content).strip()
        result = json.loads(content)
    except (AttributeError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError(
            "OpenRouter returned an invalid or empty structured response."
        ) from error
    if not isinstance(result, dict):
        raise RuntimeError("OpenRouter returned an invalid structured response.")
    required = schema.get("required", [])
    if any(key not in result for key in required):
        raise RuntimeError("OpenRouter returned incomplete structured data.")
    return result


def promise_payload(row):
    requirements = json.loads(row["requirements_json"])
    product = PRODUCTS[row["product_id"]]
    return {
        "id": row["id"],
        "original_request": row["original_request"],
        "requirements": requirements,
        "product": {**product, "price": format_money(product_price(product))},
        "status": row["status"],
        "payment_status": row["payment_status"],
        "created_at": row["created_at"],
        "approved_at": row["approved_at"],
        "paid_at": row["paid_at"],
    }


def product_price(product):
    return Decimal(product["price"])


def format_money(value):
    return f"{value.quantize(Decimal('0.01')):.2f}"


def get_cart():
    cart = session.get("cart", {})
    if not isinstance(cart, dict):
        return {}
    return cart


def build_cart_summary():
    items = []
    total = Decimal("0.00")
    item_count = 0

    for product_id, raw_quantity in get_cart().items():
        product = PRODUCTS.get(product_id)
        if not product or not product["available"]:
            continue
        try:
            quantity = int(raw_quantity)
        except (TypeError, ValueError):
            continue
        if quantity < 1 or quantity > MAX_QUANTITY:
            continue

        subtotal = product_price(product) * quantity
        items.append(
            {
                "product": product,
                "quantity": quantity,
                "subtotal": format_money(subtotal),
            }
        )
        total += subtotal
        item_count += quantity

    return {
        "items": items,
        "item_count": item_count,
        "total": format_money(total),
        "currency": CURRENCY,
    }


def validate_cart():
    summary = build_cart_summary()
    if not summary["items"]:
        raise ValueError("Your cart is empty.")
    if summary["item_count"] > MAX_CART_ITEMS:
        raise ValueError("Your cart contains too many items.")
    if len(summary["items"]) != len(get_cart()):
        raise ValueError("Your cart contains an unavailable product.")
    return summary


def get_access_token():
    if not PAYPAL_CLIENT_ID or not PAYPAL_CLIENT_SECRET:
        raise RuntimeError("PayPal credentials are not configured.")

    response = requests.post(
        f"{PAYPAL_BASE_URL}/v1/oauth2/token",
        auth=(PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET),
        data={"grant_type": "client_credentials"},
        timeout=20,
    )
    response.raise_for_status()
    access_token = response.json().get("access_token")
    if not access_token:
        raise RuntimeError("PayPal did not return an access token.")
    return access_token


def paypal_api(method, path, payload=None):
    token = get_access_token()
    response = requests.request(
        method,
        f"{PAYPAL_BASE_URL}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json=payload,
        timeout=20,
    )
    if response.status_code >= 400:
        try:
            error_data = response.json()
        except ValueError:
            error_data = {}
        raise PayPalAPIError(
            response.status_code,
            error_data.get("name", "UNKNOWN_PAYPAL_ERROR"),
            error_data.get("message", "PayPal rejected the request."),
            error_data.get("debug_id"),
        )
    return response.json()


def serialize_products():
    return [
        {
            **product,
            "price": format_money(product_price(product)),
        }
        for product in PRODUCTS.values()
    ]


@app.route("/")
def home():
    return render_template(
        "index.html",
        paypal_client_id=PAYPAL_CLIENT_ID,
    )


@app.route("/api/products")
def products():
    return jsonify(products=serialize_products(), currency=CURRENCY)


REQUIREMENTS_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": ["string", "null"]},
        "budget": {
            "type": ["object", "null"],
            "properties": {
                "amount": {"type": ["number", "null"]},
                "currency": {"type": ["string", "null"]},
            },
            "required": ["amount", "currency"],
            "additionalProperties": False,
        },
        "intended_use": {"type": ["string", "null"]},
        "required_features": {"type": "array", "items": {"type": "string"}},
        "preferred_features": {"type": "array", "items": {"type": "string"}},
        "lower_priority_features": {"type": "array", "items": {"type": "string"}},
        "expected_useful_lifetime": {"type": ["string", "null"]},
        "clarifying_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "category", "budget", "intended_use", "required_features",
        "preferred_features", "lower_priority_features",
        "expected_useful_lifetime", "clarifying_questions",
    ],
    "additionalProperties": False,
}


def fallback_requirements(text):
    parsed = parse_requirements(text)
    return {
        "category": "computer accessories" if parsed["primary_use"] == "programming" else None,
        "budget": (
            {
                "amount": float(parsed["budget"]["amount"]),
                "currency": parsed["budget"]["currency"],
            }
            if parsed["budget"]
            else None
        ),
        "intended_use": parsed["primary_use"],
        "required_features": parsed["must_have"],
        "preferred_features": parsed["important"],
        "lower_priority_features": [],
        "expected_useful_lifetime": (
            f"{parsed['useful_lifetime_goal_years']} years"
            if parsed["useful_lifetime_goal_years"]
            else None
        ),
        "clarifying_questions": [],
    }


@app.route("/api/ai/analyze-requirements", methods=["POST"])
def analyze_requirements():
    data = request.get_json(silent=True) or {}
    text = data.get("request")
    if not isinstance(text, str) or len(text.strip()) > 500:
        return jsonify(error="Shopping requests must be 1-500 characters."), 400
    try:
        result = ai_json(
            "Extract only the requirements explicitly stated or clearly implied by this shopping request.",
            {"shopping_request": text.strip()},
            REQUIREMENTS_SCHEMA,
        )
        return jsonify(requirements=result, ai_used=True)
    except RuntimeError as error:
        app.logger.info("Using rule-based requirement fallback: %s", error)
        try:
            return jsonify(
                requirements=fallback_requirements(text.strip()),
                ai_used=False,
                fallback_message=str(error),
            )
        except (TypeError, ValueError, InvalidOperation) as parse_error:
            return jsonify(error=str(parse_error)), 400
    except Exception:
        app.logger.exception("OpenRouter requirement analysis failed.")
        return jsonify(error="AI analysis is temporarily unavailable. Try again or use the rule-based fallback."), 502


@app.route("/api/ai/explain-product", methods=["POST"])
def explain_product():
    data = request.get_json(silent=True) or {}
    product = PRODUCTS.get(data.get("product_id"))
    if not product:
        return jsonify(error="Unknown product."), 400
    try:
        requirements = data.get("requirements")
        if not isinstance(requirements, dict):
            requirements = fallback_requirements(data.get("request", ""))
        details = match_details(product, requirements)
        payload = {
            "product": {**product, "price": format_money(product_price(product))},
            "validated_requirements": requirements,
            "deterministic_match": details,
        }
        explanation = ai_json(
            "Explain this deterministic product match using only the supplied facts. Do not change the score.",
            payload,
            {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "tradeoffs": {"type": "array", "items": {"type": "string"}},
                    "missing_information": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["summary", "tradeoffs", "missing_information"],
                "additionalProperties": False,
            },
        )
        return jsonify(product=payload["product"], deterministic_match=details, explanation=explanation, ai_used=True)
    except RuntimeError as error:
        return jsonify(product={**product, "price": format_money(product_price(product))}, deterministic_match=match_details(product, fallback_requirements(data.get("request", ""))), explanation={"summary": "The deterministic match is shown above. AI explanation is unavailable.", "tradeoffs": [], "missing_information": ["AI explanation unavailable."]}, ai_used=False, fallback_message=str(error))
    except Exception:
        app.logger.exception("OpenRouter product explanation failed.")
        return jsonify(error="The product explanation is temporarily unavailable."), 502


@app.route("/api/ai/evaluate-purchase", methods=["POST"])
def evaluate_purchase():
    data = request.get_json(silent=True) or {}
    promise_id = data.get("promise_id")
    with database() as connection:
        row = connection.execute("SELECT * FROM promises WHERE id = ?", (promise_id,)).fetchone()
        alerts = connection.execute(
            "SELECT kind, message, source, verified, created_at FROM guardian_alerts WHERE promise_id = ? ORDER BY id",
            (promise_id,),
        ).fetchall()
    if not row:
        return jsonify(error="Purchase Promise not found."), 404
    product = PRODUCTS.get(row["product_id"])
    payload = {
        "promise": promise_payload(row),
        "observations": [dict(alert) for alert in alerts],
        "current_verified_product": {**product, "price": format_money(product_price(product))},
    }
    try:
        assessment = ai_json(
            "Evaluate whether the purchase still appears reasonable. Separate facts, demo observations, unknowns, and assumptions.",
            payload,
            {
                "type": "object",
                "properties": {
                    "assessment": {"type": "string"},
                    "facts": {"type": "array", "items": {"type": "string"}},
                    "unknowns": {"type": "array", "items": {"type": "string"}},
                    "next_steps": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["assessment", "facts", "unknowns", "next_steps"],
                "additionalProperties": False,
            },
        )
        return jsonify(assessment=assessment, ai_used=True)
    except RuntimeError as error:
        return jsonify(
            assessment={
                "assessment": "The recorded demo data still supports the purchase, but live retailer information is unavailable.",
                "facts": [f"Recorded product: {product['name']}", f"Recorded price: ${product['price']} USD"],
                "unknowns": ["Current retailer price", "Warranty", "Repairability"],
                "next_steps": ["Treat this as a demo assessment and verify current information before relying on it."],
            },
            ai_used=False,
            fallback_message=str(error),
        )
    except Exception:
        app.logger.exception("OpenRouter purchase evaluation failed.")
        return jsonify(error="The purchase evaluation is temporarily unavailable."), 502


@app.route("/api/recommendations", methods=["POST"])
def recommendations():
    data = request.get_json(silent=True) or {}
    try:
        requirements = parse_requirements(data.get("request", ""))
    except (TypeError, ValueError, InvalidOperation) as error:
        return jsonify(error=str(error)), 400
    return jsonify(
        requirements=requirements,
        products=recommendation_for(requirements),
        data_status="Demo catalogue; product facts are simulated and not live retailer data.",
    )


@app.route("/api/promises", methods=["POST"])
def create_promise():
    data = request.get_json(silent=True) or {}
    original_request = data.get("original_request", "")
    product_id = data.get("product_id")
    try:
        requirements = parse_requirements(original_request)
    except (TypeError, ValueError, InvalidOperation) as error:
        return jsonify(error=str(error)), 400
    product = PRODUCTS.get(product_id)
    if not product or not product["available"]:
        return jsonify(error="Select an available catalogue product."), 400
    with database() as connection:
        cursor = connection.execute(
            """
            INSERT INTO promises
            (original_request, requirements_json, product_id, status, payment_status, created_at)
            VALUES (?, ?, ?, 'draft', 'not_started', ?)
            """,
            (original_request.strip(), json.dumps(requirements), product_id, utc_now()),
        )
        row = connection.execute(
            "SELECT * FROM promises WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    return jsonify(promise_payload(row)), 201


@app.route("/api/promises/<int:promise_id>")
def get_promise(promise_id):
    with database() as connection:
        row = connection.execute(
            "SELECT * FROM promises WHERE id = ?", (promise_id,)
        ).fetchone()
    if not row:
        return jsonify(error="Purchase Promise not found."), 404
    return jsonify(promise_payload(row))


@app.route("/api/promises/<int:promise_id>/approve", methods=["POST"])
def approve_promise(promise_id):
    with database() as connection:
        row = connection.execute(
            "SELECT * FROM promises WHERE id = ?", (promise_id,)
        ).fetchone()
    if not row:
        return jsonify(error="Purchase Promise not found."), 404
    if row["status"] not in {"draft", "approved"}:
        return jsonify(error="This Purchase Promise is no longer available for checkout."), 409
    session["cart"] = {row["product_id"]: 1}
    session["approved_promise_id"] = promise_id
    with database() as connection:
        connection.execute(
            "UPDATE promises SET status = 'approved', approved_at = ? WHERE id = ?",
            (utc_now(), promise_id),
        )
        row = connection.execute(
            "SELECT * FROM promises WHERE id = ?", (promise_id,)
        ).fetchone()
    return jsonify(promise_payload(row))


@app.route("/api/guardian/<int:promise_id>")
def guardian(promise_id):
    with database() as connection:
        row = connection.execute(
            "SELECT * FROM promises WHERE id = ?", (promise_id,)
        ).fetchone()
        alerts = connection.execute(
            "SELECT * FROM guardian_alerts WHERE promise_id = ? ORDER BY id DESC",
            (promise_id,),
        ).fetchall()
    if not row:
        return jsonify(error="Purchase Promise not found."), 404
    return jsonify(
        promise=promise_payload(row),
        alerts=[dict(alert) for alert in alerts],
        monitoring_status="Demo monitoring only; no retailer API is configured.",
    )


@app.route("/api/guardian/<int:promise_id>/check", methods=["POST"])
def guardian_check(promise_id):
    with database() as connection:
        row = connection.execute(
            "SELECT * FROM promises WHERE id = ?", (promise_id,)
        ).fetchone()
    if not row:
        return jsonify(error="Purchase Promise not found."), 404
    product = PRODUCTS[row["product_id"]]
    message = (
        f"Demo observation: {product['name']} may be listed at a different price in a future check. "
        "This simulated alert is not a guaranteed refund or price adjustment."
    )
    with database() as connection:
        connection.execute(
            """
            INSERT INTO guardian_alerts
            (promise_id, kind, message, source, verified, created_at)
            VALUES (?, 'price_observation', ?, 'Deterministic demo event', 0, ?)
            """,
            (promise_id, message, utc_now()),
        )
    return jsonify(message=message, simulated=True)


@app.route("/api/guardian/<int:promise_id>/sense")
def guardian_sense(promise_id):
    with database() as connection:
        row = connection.execute(
            "SELECT * FROM promises WHERE id = ?", (promise_id,)
        ).fetchone()
    if not row:
        return jsonify(error="Purchase Promise not found."), 404
    return jsonify(
        answer=(
            f"Based on the original request — “{row['original_request']}” — "
            "this purchase still appears reasonable using the recorded demo data. "
            "Live price, warranty, and repairability data are not available."
        ),
        source="Saved Purchase Promise and demo catalogue",
    )


@app.route("/api/cart", methods=["GET", "POST"])
def cart():
    if request.method == "GET":
        return jsonify(build_cart_summary())

    data = request.get_json(silent=True) or {}
    action = data.get("action")
    product_id = data.get("product_id")
    if action not in {"add", "update", "remove"}:
        return jsonify(error="Cart action must be add, update, or remove."), 400
    if not isinstance(product_id, str) or product_id not in PRODUCTS:
        return jsonify(error="Unknown product."), 400

    product = PRODUCTS[product_id]
    if not product["available"]:
        return jsonify(error="That product is currently unavailable."), 409

    cart_data = get_cart().copy()
    if action == "remove":
        cart_data.pop(product_id, None)
    else:
        try:
            quantity = int(data.get("quantity", 1))
        except (TypeError, ValueError):
            return jsonify(error="Quantity must be a whole number."), 400
        if quantity < 1 or quantity > MAX_QUANTITY:
            return jsonify(
                error=f"Quantity must be between 1 and {MAX_QUANTITY}."
            ), 400
        if action == "add":
            quantity += int(cart_data.get(product_id, 0))
            if quantity > MAX_QUANTITY:
                return jsonify(
                    error=f"You can add at most {MAX_QUANTITY} of one product."
                ), 400
        cart_data[product_id] = quantity

    session["cart"] = cart_data
    return jsonify(build_cart_summary())


@app.route("/api/create-order", methods=["POST"])
def create_order():
    if not credentials_ready():
        return jsonify(error="PayPal configuration is incomplete."), 500
    try:
        summary = validate_cart()
        paypal_items = [
            {
                "name": item["product"]["name"],
                "unit_amount": {
                    "currency_code": CURRENCY,
                    "value": item["product"]["price"],
                },
                "quantity": str(item["quantity"]),
            }
            for item in summary["items"]
        ]
        order = paypal_api(
            "POST",
            "/v2/checkout/orders",
            {
                "intent": "CAPTURE",
                "purchase_units": [
                    {
                        "description": "SmartCart AI shopping cart",
                        "amount": {
                            "currency_code": CURRENCY,
                            "value": summary["total"],
                            "breakdown": {
                                "item_total": {
                                    "currency_code": CURRENCY,
                                    "value": summary["total"],
                                }
                            },
                        },
                        "items": paypal_items,
                    }
                ],
            },
        )
        order_id = order.get("id")
        if not order_id:
            return jsonify(error="PayPal did not return an order ID."), 502

        orders = session.get("orders", {})
        orders[order_id] = {
            "total": summary["total"],
            "currency": CURRENCY,
            "promise_id": session.get("approved_promise_id"),
            "items": [
                {"product_id": item["product"]["id"], "quantity": item["quantity"]}
                for item in summary["items"]
            ],
        }
        session["orders"] = orders
        return jsonify(id=order_id, total=summary["total"], currency=CURRENCY)
    except ValueError as error:
        return jsonify(error=str(error)), 400
    except PayPalAPIError as error:
        app.logger.error(
            "PayPal order rejected: status=%s name=%s message=%s debug_id=%s",
            error.status_code,
            error.name,
            error,
            error.debug_id or "none",
        )
        return jsonify(
            error=f"PayPal rejected the order request. {error.name}: {error}"
        ), 502
    except (requests.RequestException, RuntimeError, InvalidOperation):
        app.logger.exception("PayPal order creation request failed.")
        return jsonify(
            error="Could not create the test order. Check server logs."
        ), 502


@app.route("/api/capture-order", methods=["POST"])
def capture_order():
    if not credentials_ready():
        return jsonify(error="PayPal configuration is incomplete."), 500
    data = request.get_json(silent=True) or {}
    order_id = data.get("orderID")
    orders = session.get("orders", {})
    saved_order = orders.get(order_id) if isinstance(order_id, str) else None
    if not saved_order:
        return jsonify(error="Order not recognized. Please try checkout again."), 400

    try:
        result = paypal_api(
            "POST",
            f"/v2/checkout/orders/{order_id}/capture",
            {},
        )
        expected_total = saved_order["total"]
        captures = [
            capture
            for unit in result.get("purchase_units", [])
            for capture in unit.get("payments", {}).get("captures", [])
        ]
        valid_capture = any(
            capture.get("status") == "COMPLETED"
            and capture.get("amount", {}).get("currency_code") == CURRENCY
            and capture.get("amount", {}).get("value") == expected_total
            for capture in captures
        )
        if result.get("status") != "COMPLETED" or not valid_capture:
            app.logger.error("PayPal capture did not match expected order.")
            return jsonify(
                error="Payment amount or status could not be verified."
            ), 400

        orders.pop(order_id, None)
        session["orders"] = orders
        session["cart"] = {}
        promise_id = saved_order.get("promise_id")
        if promise_id:
            with database() as connection:
                connection.execute(
                    """
                    UPDATE promises
                    SET status = 'paid', payment_status = 'completed', paid_at = ?
                    WHERE id = ?
                    """,
                    (utc_now(), promise_id),
                )
        return jsonify(
            status="COMPLETED",
            message="Step 3/3: Sandbox payment completed successfully!",
        )
    except PayPalAPIError as error:
        app.logger.error(
            "PayPal capture rejected: status=%s name=%s message=%s debug_id=%s",
            error.status_code,
            error.name,
            error,
            error.debug_id or "none",
        )
        return jsonify(
            error=(
                "PayPal rejected the capture request. "
                f"{error.name}: {error}"
            )
        ), 502
    except requests.RequestException:
        app.logger.exception("PayPal capture request failed.")
        return jsonify(
            error="Could not verify the payment. Check server logs."
        ), 502


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if not credentials_ready():
        print(
            "Warning: check PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET, "
            "and FLASK_SECRET_KEY in .env"
        )
    app.run(debug=True)
