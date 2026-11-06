import logging
import json
import os
import re
import sqlite3
import hashlib
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, session

import httpx


try:
    from openai import (
        APIConnectionError,
        APIStatusError,
        APITimeoutError,
        AuthenticationError,
        OpenAI,
        RateLimitError,
    )
except ImportError:
    OpenAI = None
    APIConnectionError = APIStatusError = APITimeoutError = Exception
    AuthenticationError = RateLimitError = Exception

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
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
PRODUCT_PROVIDER_URL = os.getenv("PRODUCT_PROVIDER_URL", "").strip()
FAKE_STORE_API_URL = os.getenv(
    "FAKE_STORE_API_URL", "https://fakestoreapi.com/products"
).strip()
FAKE_STORE_CACHE_SECONDS = 900
FAKE_STORE_CURRENCY = os.getenv("FAKE_STORE_CURRENCY", "").strip().upper() or None
DATABASE_PATH = os.getenv(
    "SMARTCART_DATABASE",
    os.path.join(os.path.dirname(__file__), "smartcart.db"),
)

CURRENCY = "USD"
MAX_QUANTITY = 20
MAX_CART_ITEMS = 100
_openrouter_sdk_client = None


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
    "classic-handbag": {
        "id": "classic-handbag",
        "name": "Classic Everyday Handbag",
        "description": "A structured handbag with room for daily essentials.",
        "category": "Handbags",
        "price": "89.99",
        "image": "https://images.unsplash.com/photo-1584917865442-de89df76afd3?auto=format&fit=crop&w=700&q=80",
        "available": True,
        "specs": {"material": "Faux leather", "style": "Structured", "carry": "Shoulder or hand"},
    },
    "travel-handbag": {
        "id": "travel-handbag",
        "name": "Convertible Travel Handbag",
        "description": "A spacious handbag with an adjustable strap for travel and everyday use.",
        "category": "Handbags",
        "price": "129.00",
        "image": "https://images.unsplash.com/photo-1594223274512-ad4803739b7c?auto=format&fit=crop&w=700&q=80",
        "available": True,
        "specs": {"material": "Canvas", "style": "Convertible", "pockets": "Multiple"},
    },
}


def is_purchase_eligible(product):
    return bool(
        product.get("product_type") == "verified"
        and product.get("price") is not None
        and product.get("currency") == CURRENCY
        and product.get("seller")
        and product.get("product_url")
        and product.get("source_type")
        and product.get("verification_status") == "verified"
    )


def prepare_catalogue_products():
    for product in PRODUCTS.values():
        product.setdefault("product_type", "verified")
        product.setdefault("currency", CURRENCY)
        product.setdefault("seller", "smartcart catalogue")
        product.setdefault("product_url", f"/products/{product['id']}")
        product.setdefault("source_type", "smartcart_server_catalogue")
        product.setdefault("verification_status", "verified")


prepare_catalogue_products()


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
            CREATE TABLE IF NOT EXISTS discovered_products (
                product_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                brand TEXT,
                category TEXT,
                description TEXT,
                price TEXT,
                currency TEXT,
                image_url TEXT,
                product_url TEXT,
                seller TEXT,
                rating REAL,
                review_count INTEGER,
                specifications_json TEXT NOT NULL,
                features_json TEXT NOT NULL,
                source_type TEXT NOT NULL,
                verification_status TEXT NOT NULL,
                discovered_at TEXT NOT NULL,
                search_query TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_discovered_products_source
                ON discovered_products(source_type, discovered_at);
            """
        )
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(discovered_products)"
            ).fetchall()
        }
        if "rating" not in columns:
            connection.execute("ALTER TABLE discovered_products ADD COLUMN rating REAL")
        if "review_count" not in columns:
            connection.execute(
                "ALTER TABLE discovered_products ADD COLUMN review_count INTEGER"
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
    category = str(requirements.get("category") or "").strip().lower()
    products = list(PRODUCTS.values())
    if category:
        category_matches = [
            product
            for product in products
            if category in product["name"].lower()
            or category in product["category"].lower()
            or any(category in str(value).lower() for value in product["specs"].values())
        ]
        if not category_matches:
            return []
        products = category_matches
    for product in products:
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
    global _openrouter_sdk_client
    if not OPENROUTER_API_KEY or not OPENROUTER_MODEL or OpenAI is None:
        return None

    if not OPENROUTER_API_KEY.startswith("sk-or-v1-"):
        raise RuntimeError(
            "OpenRouter API key format is invalid. "
            "Check your key at openrouter.ai/keys."
        )

    if _openrouter_sdk_client is None:
        # OpenRouter requests must not inherit system proxy settings. The
        # direct requests integration is also verified with trust_env=False.
        http_client = httpx.Client(
            trust_env=False,
            timeout=httpx.Timeout(20.0, connect=5.0),
        )
        _openrouter_sdk_client = OpenAI(
            api_key=OPENROUTER_API_KEY,
            base_url=OPENROUTER_BASE_URL,
            http_client=http_client,
            max_retries=0,
        )
    return _openrouter_sdk_client


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
                        "You are the smartcart shopping assistant. "
                        "Return only valid JSON matching the supplied schema. "
                        "Use null or empty arrays when information is unknown. "
                        "Never invent product facts, prices, ratings, availability, or guarantees."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": task,
                            "data": payload,
                            "schema": schema,
                        }
                    ),
                },
            ],
        )
    except AuthenticationError as error:
        app.logger.warning("OpenRouter authentication failed.")
        raise RuntimeError(
            "OpenRouter authentication failed. Check OPENROUTER_API_KEY."
        ) from error
    except RateLimitError as error:
        app.logger.warning("OpenRouter rate limit reached.")
        raise RuntimeError(
            "OpenRouter rate limit reached. Try again later."
        ) from error
    except (APITimeoutError, httpx.TimeoutException) as error:
        app.logger.warning("OpenRouter request timed out.")
        raise RuntimeError(
            "OpenRouter request timed out. Using the labelled rule-based fallback."
        ) from error
    except (APIConnectionError, httpx.NetworkError) as error:
        app.logger.warning("OpenRouter connection failed: %s", type(error).__name__)
        raise RuntimeError(
            "OpenRouter connection failed. Using the labelled rule-based fallback."
        ) from error
    except APIStatusError as error:
        status_code = getattr(error, "status_code", "unknown")
        app.logger.warning("OpenRouter API returned status %s.", status_code)
        raise RuntimeError(
            f"OpenRouter API returned status {status_code}. "
            "Using the labelled rule-based fallback."
        ) from error
    except Exception as error:
        app.logger.warning(
            "OpenRouter request failed unexpectedly: %s",
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
        app.logger.warning("OpenRouter returned invalid structured data.")
        raise RuntimeError(
            "OpenRouter returned an invalid or empty structured response."
        ) from error
    if not isinstance(result, dict):
        app.logger.warning("OpenRouter returned a non-object JSON response.")
        raise RuntimeError("OpenRouter returned an invalid structured response.")
    required = schema.get("required", [])
    if any(key not in result for key in required):
        app.logger.warning("OpenRouter response omitted required structured fields.")
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
        if not product or not product["available"] or not is_purchase_eligible(product):
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
        "budget": session.get("cart_budget"),
        "over_budget": (
            bool(session.get("cart_budget"))
            and session["cart_budget"].get("currency") == CURRENCY
            and total > Decimal(str(session["cart_budget"]["amount"]))
        ),
    }


def validate_cart():
    summary = build_cart_summary()
    if not summary["items"]:
        raise ValueError("Your cart is empty.")
    if summary["item_count"] > MAX_CART_ITEMS:
        raise ValueError("Your cart contains too many items.")
    if len(summary["items"]) != len(get_cart()):
        raise ValueError("Your cart contains an unavailable product.")
    if summary["over_budget"]:
        raise ValueError(
            "Your cart exceeds the saved budget. Remove an item or update your budget before checkout."
        )
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
            "purchase_eligible": is_purchase_eligible(product),
            "purchase_disabled_reason": (
                None
                if is_purchase_eligible(product)
                else "Synthetic or unverified product; no real purchase is available."
            ),
        }
        for product in PRODUCTS.values()
    ]


PRODUCT_DISCOVERY_SCHEMA = {
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
        "missing_information": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "category", "budget", "intended_use", "required_features",
        "preferred_features", "missing_information",
    ],
    "additionalProperties": False,
}

SYNTHETIC_PRODUCT_SCHEMA = {
    "type": "object",
    "properties": {
        "products": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "category": {"type": "string"},
                    "description": {"type": "string"},
                    "features": {"type": "array", "items": {"type": "string"}},
                    "specifications": {"type": "object"},
                },
                "required": ["name", "category", "description", "features", "specifications"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["products"],
    "additionalProperties": False,
}


def product_contract(product, source_type=None, verification_status=None):
    source_type = source_type or "local_demo"
    verification_status = verification_status or product.get(
        "verification_status", "demo"
    )
    return {
        "product_id": product["id"],
        "name": product["name"],
        "brand": product.get("brand"),
        "category": product.get("category"),
        "description": product.get("description"),
        "price": format_money(product_price(product)) if product.get("price") is not None else None,
        "currency": CURRENCY if product.get("price") is not None else None,
        "image_url": product.get("image"),
        "product_url": product.get("product_url"),
        "seller": product.get("seller"),
        "specifications": product.get("specs", {}),
        "features": product.get("features", []),
        "source_type": source_type,
        "verification_status": verification_status,
        "product_type": product.get("product_type", "synthetic"),
        "purchase_eligible": is_purchase_eligible(product),
        "purchase_disabled_reason": (
            None
            if is_purchase_eligible(product)
            else "Synthetic or unverified product; no real purchase is available."
        ),
    }


def deterministic_product_match(product, requirements):
    budget = requirements.get("budget") or {}
    raw_amount = budget.get("amount", budget.get("max_price"))
    amount = Decimal(str(raw_amount)) if budget.get("currency") == CURRENCY and raw_amount is not None else None
    score = 0
    reasons = []
    price = product.get("price")
    if amount is not None and price is not None:
        if Decimal(str(price)) <= amount:
            score += 50
            reasons.append("Within the stated budget.")
        else:
            reasons.append("Above the stated budget.")
    else:
        reasons.append("Price is unknown; verify before purchase.")
    category = str(requirements.get("category") or "").lower()
    if category and category in str(product.get("category") or "").lower():
        score += 25
        reasons.append("Category matches the request.")
    features = " ".join(product.get("features", []) + list(product.get("specs", {}).values())).lower()
    required = requirements.get("required_features", [])
    preferred = requirements.get("preferred_features", [])
    required_hits = sum(str(item).lower() in features for item in required)
    preferred_hits = sum(str(item).lower() in features for item in preferred)
    if required:
        score += int(20 * required_hits / len(required))
    score += int(5 * preferred_hits / len(preferred)) if preferred else 0
    return min(score, 100), reasons


def store_discovered_product(product, query):
    with database() as connection:
        connection.execute(
            """
            INSERT INTO discovered_products
            (product_id, name, brand, category, description, price, currency,
             image_url, product_url, seller, rating, review_count,
             specifications_json, features_json,
             source_type, verification_status, discovered_at, search_query)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_id) DO UPDATE SET
              name=excluded.name, brand=excluded.brand, category=excluded.category,
              description=excluded.description, price=excluded.price,
              currency=excluded.currency, image_url=excluded.image_url,
              product_url=excluded.product_url, seller=excluded.seller,
              rating=excluded.rating, review_count=excluded.review_count,
              specifications_json=excluded.specifications_json,
              features_json=excluded.features_json,
              source_type=excluded.source_type,
              verification_status=excluded.verification_status,
              discovered_at=excluded.discovered_at,
              search_query=excluded.search_query
            """,
            (
                product["product_id"], product["name"], product.get("brand"),
                product.get("category"), product.get("description"),
                product.get("price"), product.get("currency"), product.get("image_url"),
                product.get("product_url"), product.get("seller"),
                product.get("rating"), product.get("review_count"),
                json.dumps(product.get("specifications", {})),
                json.dumps(product.get("features", [])), product["source_type"],
                product["verification_status"], utc_now(), query,
            ),
        )


def external_products(query):
    if not PRODUCT_PROVIDER_URL:
        return []
    response = requests.get(PRODUCT_PROVIDER_URL, params={"q": query}, timeout=10)
    response.raise_for_status()
    data = response.json()
    raw_products = data.get("products", data) if isinstance(data, dict) else data
    if not isinstance(raw_products, list):
        raise ValueError("Configured product provider returned an invalid product list.")
    products = []
    for raw in raw_products:
        if not isinstance(raw, dict) or not raw.get("name"):
            continue
        product_id = str(raw.get("product_id") or raw.get("id") or hashlib.sha256(
            f"{raw['name']}|{raw.get('product_url', '')}".encode()
        ).hexdigest()[:16])
        products.append({
            "product_id": product_id, "name": raw["name"], "brand": raw.get("brand"),
            "category": raw.get("category"), "description": raw.get("description"),
            "price": raw.get("price"), "currency": raw.get("currency"),
            "image_url": raw.get("image_url"), "product_url": raw.get("product_url"),
            "seller": raw.get("seller"), "specifications": raw.get("specifications", {}),
            "features": raw.get("features", []), "source_type": "external_verified",
            "verification_status": "provider_reported",
            "product_type": "verified",
        })
    return products


def _cached_fake_store_products():
    with database() as connection:
        rows = connection.execute(
            """
            SELECT * FROM discovered_products
            WHERE source_type = 'external_demo_api'
            ORDER BY product_id
            """
        ).fetchall()
    products = []
    for row in rows:
        item = dict(row)
        item["specifications"] = json.loads(item.pop("specifications_json"))
        item["features"] = json.loads(item.pop("features_json"))
        item["title"] = item["name"]
        item["product_type"] = "external_demo"
        item["purchase_eligible"] = False
        item["purchase_disabled_reason"] = (
            "Fake Store API is a demonstration catalogue; it has no retailer checkout."
        )
        products.append(item)
    return products


def fake_store_products():
    cached = _cached_fake_store_products()
    fresh = False
    if cached:
        try:
            newest = max(datetime.fromisoformat(item["discovered_at"]) for item in cached)
            fresh = (datetime.now(timezone.utc) - newest).total_seconds() < FAKE_STORE_CACHE_SECONDS
        except (KeyError, TypeError, ValueError):
            fresh = False
    if fresh:
        return cached

    try:
        response = requests.get(FAKE_STORE_API_URL, timeout=10)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("Fake Store API returned an invalid product list.")
        products = []
        for raw in payload:
            if not isinstance(raw, dict) or not raw.get("id") or not raw.get("title"):
                continue
            raw_price = raw.get("price")
            try:
                price = format_money(Decimal(str(raw_price))) if raw_price is not None else None
            except (InvalidOperation, ValueError):
                price = None
            rating = raw.get("rating") if isinstance(raw.get("rating"), dict) else {}
            product = {
                "product_id": f"fake-store-{raw['id']}",
                "name": str(raw["title"]),
                "title": str(raw["title"]),
                "brand": None,
                "category": raw.get("category"),
                "description": raw.get("description"),
                "price": price,
                "currency": FAKE_STORE_CURRENCY,
                "image_url": raw.get("image"),
                "product_url": None,
                "seller": None,
                "specifications": {},
                "features": [],
                "rating": rating.get("rate"),
                "review_count": rating.get("count"),
                "source_type": "external_demo_api",
                "verification_status": "demo_catalogue",
                "product_type": "external_demo",
                "purchase_eligible": False,
                "purchase_disabled_reason": (
                    "Fake Store API is a demonstration catalogue; it has no retailer checkout."
                ),
            }
            products.append(product)
            store_discovered_product(product, "Fake Store API catalogue")
        return products
    except (requests.RequestException, ValueError, TypeError) as error:
        app.logger.warning("Fake Store API unavailable: %s", type(error).__name__)
        return cached


def filter_external_products(products, requirements):
    category = str(requirements.get("category") or "").lower()
    budget = requirements.get("budget") or {}
    amount = budget.get("amount")
    budget_amount = (
        Decimal(str(amount))
        if amount is not None and budget.get("currency") == FAKE_STORE_CURRENCY
        else None
    )
    filtered = []
    for product in products:
        product_category = str(product.get("category") or "").lower()
        if category and category not in product_category and category not in product["name"].lower():
            continue
        score = 0
        reasons = ["Fake Store API demo catalogue; no retailer purchase is available."]
        if budget_amount is not None and product.get("price") is not None:
            if Decimal(str(product["price"])) <= budget_amount:
                score += 50
                reasons.append("Within the stated budget.")
            else:
                reasons.append("Above the stated budget.")
        else:
            reasons.append("Currency is unknown; budget comparison was not performed.")
        product = dict(product)
        product["match_score"] = score
        product["match_explanation"] = reasons
        filtered.append(product)
    return sorted(filtered, key=lambda item: item["match_score"], reverse=True)


def synthetic_products(requirements, query):
    category = requirements.get("category") or "requested product"
    try:
        result = ai_json(
            "Generate up to three clearly synthetic demonstration product concepts. "
            "Never provide brands, prices, ratings, stock, sellers, links, or claims of verification.",
            {"category": category, "requirements": requirements},
            SYNTHETIC_PRODUCT_SCHEMA,
        )
        raw_products = result["products"]
    except RuntimeError:
        raw_products = [
            {
                "name": f"{str(category).title()} Demonstration Option {index}",
                "category": category,
                "description": "Synthetic concept for demonstrating discovery; not a real listing.",
                "features": [],
                "specifications": {},
            }
            for index in range(1, 4)
        ]
    products = []
    for index, raw in enumerate(raw_products[:3], 1):
        product_id = "synthetic-" + hashlib.sha256(
            f"{query}|{raw['name']}|{index}".encode()
        ).hexdigest()[:16]
        products.append({
            "product_id": product_id, "name": raw["name"], "brand": None,
            "category": raw.get("category", category),
            "description": raw["description"], "price": None, "currency": None,
            "image_url": None, "product_url": None, "seller": None,
            "specifications": raw.get("specifications", {}),
            "features": raw.get("features", []), "source_type": "synthetic_demo",
            "verification_status": "synthetic_not_verified",
            "product_type": "synthetic",
            "purchase_eligible": False,
            "purchase_disabled_reason": "Synthetic demonstration concept; it is not a real purchasable listing.",
        })
    return products


def discover_products(query):
    try:
        requirements = ai_json(
            "Extract product discovery requirements. Use null for unknown values.",
            {"search": query},
            PRODUCT_DISCOVERY_SCHEMA,
        )
    except RuntimeError as error:
        app.logger.info("Using rule-based discovery requirements: %s", error)
        fallback = fallback_requirements(query)
        requirements = {
            "category": None,
            "budget": fallback["budget"],
            "intended_use": fallback["intended_use"],
            "required_features": fallback["required_features"],
            "preferred_features": fallback["preferred_features"],
            "missing_information": fallback.get("missing_information", fallback.get("unknown", [])),
        }
        category_match = re.search(
            r"\b(handbags?|laptops?|computers?|earbuds?|headphones?|keyboards?|backpacks?)\b",
            query.lower(),
        )
        if category_match:
            requirements["category"] = category_match.group(1).rstrip("s")
    local = []
    category = str(requirements.get("category") or "").lower()
    for product in PRODUCTS.values():
        if not category or category in product["name"].lower() or category in product["category"].lower():
            item = product_contract(product)
            item["match_score"], reasons = deterministic_product_match(product, requirements)
            item["match_explanation"] = reasons
            local.append(item)
    fake_store = filter_external_products(fake_store_products(), requirements)
    configured_external = external_products(query)
    products = local + fake_store + configured_external
    if not products:
        products = synthetic_products(requirements, query)
    for product in products:
        if "match_score" not in product:
            product["match_score"], product["match_explanation"] = deterministic_product_match(product, requirements)
        store_discovered_product(product, query)
    return requirements, sorted(products, key=lambda item: item["match_score"], reverse=True)


@app.route("/")
def home():
    return render_template(
        "index.html",
        paypal_client_id=PAYPAL_CLIENT_ID,
    )


@app.route("/api/products")
def products():
    return jsonify(products=serialize_products(), currency=CURRENCY)


@app.route("/api/products/fake-store")
def fake_store_catalogue():
    return jsonify(
        products=fake_store_products(),
        currency=FAKE_STORE_CURRENCY,
        data_status="Fake Store API demonstration data; products are not purchasable.",
    )


@app.route("/api/products/discover", methods=["POST"])
def discover_products_endpoint():
    data = request.get_json(silent=True) or {}
    query = data.get("query", data.get("request", ""))
    if not isinstance(query, str) or not query.strip() or len(query.strip()) > 500:
        return jsonify(error="Search must be 1-500 characters."), 400
    query = query.strip()
    try:
        requirements, results = discover_products(query)
        return jsonify(
            query=query,
            requirements=requirements,
            products=results,
            data_status="Prices, sellers, stock, and links are only shown when supplied by a configured provider; synthetic products are not purchasable.",
        )
    except (requests.RequestException, ValueError) as error:
        app.logger.warning("Product discovery provider failed: %s", type(error).__name__)
        return jsonify(error="Product discovery is temporarily unavailable. Try again."), 502


@app.route("/api/products/search")
def search_products():
    query = request.args.get("q", "").strip().lower()
    if not query:
        return jsonify(products=[]), 200
    with database() as connection:
        rows = connection.execute(
            """
            SELECT * FROM discovered_products
            WHERE lower(name) LIKE ? OR lower(category) LIKE ?
            ORDER BY discovered_at DESC LIMIT 50
            """,
            (f"%{query}%", f"%{query}%"),
        ).fetchall()
    results = []
    for row in rows:
        item = dict(row)
        item["specifications"] = json.loads(item.pop("specifications_json"))
        item["features"] = json.loads(item.pop("features_json"))
        results.append(item)
    return jsonify(products=results)


@app.route("/api/products/<product_id>")
def product_details(product_id):
    if product_id in PRODUCTS:
        return jsonify(product_contract(PRODUCTS[product_id]))
    with database() as connection:
        row = connection.execute(
            "SELECT * FROM discovered_products WHERE product_id = ?", (product_id,)
        ).fetchone()
    if not row:
        return jsonify(error="Product not found."), 404
    item = dict(row)
    item["specifications"] = json.loads(item.pop("specifications_json"))
    item["features"] = json.loads(item.pop("features_json"))
    return jsonify(item)


@app.route("/api/products/source-status")
def product_source_status():
    return jsonify(
        local_catalogue={"configured": True, "verification_status": "demo"},
        external_provider={
            "configured": bool(PRODUCT_PROVIDER_URL),
            "verification_status": "provider_reported" if PRODUCT_PROVIDER_URL else None,
        },
        fake_store_api={
            "configured": bool(FAKE_STORE_API_URL),
            "verification_status": "demo_catalogue",
            "currency": FAKE_STORE_CURRENCY,
            "purchase_available": False,
        },
        synthetic_provider={
            "configured": bool(OPENROUTER_API_KEY),
            "verification_status": "synthetic_not_verified",
        },
    )


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
        "missing_information": parsed.get("unknown", []),
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
    except Exception as error:
        app.logger.exception("OpenRouter product explanation failed.")
        return jsonify(
            product={**product, "price": format_money(product_price(product))},
            deterministic_match=match_details(product, fallback_requirements(data.get("request", ""))),
            explanation={
                "summary": "The deterministic match is shown above. AI explanation is unavailable.",
                "tradeoffs": [],
                "missing_information": ["AI explanation unavailable."],
            },
            ai_used=False,
            fallback_message="AI explanation is temporarily unavailable. The deterministic match is shown instead.",
        )


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
    supplied_requirements = data.get("requirements")
    if isinstance(supplied_requirements, dict):
        requirements["category"] = supplied_requirements.get("category")
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
    if not product or not product["available"] or not is_purchase_eligible(product):
        return jsonify(error="Select an available verified product."), 400
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
    if not is_purchase_eligible(PRODUCTS.get(row["product_id"], {})):
        return jsonify(error="This product is not eligible for checkout."), 409
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
    if not is_purchase_eligible(product):
        return jsonify(
            error="This is a synthetic or unverified demonstration product and cannot be purchased."
        ), 409

    budget = data.get("budget")
    if budget is not None:
        if (
            not isinstance(budget, dict)
            or budget.get("currency") != CURRENCY
            or not isinstance(budget.get("amount"), (int, float, str))
        ):
            return jsonify(error="Budget must contain a valid USD amount."), 400
        try:
            budget_amount = Decimal(str(budget["amount"]))
        except InvalidOperation:
            return jsonify(error="Budget must contain a valid USD amount."), 400
        if budget_amount < 0:
            return jsonify(error="Budget cannot be negative."), 400
        session["cart_budget"] = {
            "amount": format_money(budget_amount),
            "currency": CURRENCY,
        }

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
                        "description": "smartcart shopping cart",
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
