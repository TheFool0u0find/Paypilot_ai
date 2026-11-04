const catalogueElement = document.getElementById("catalogue");
const cartItemsElement = document.getElementById("cart-items");
const cartCountElement = document.getElementById("cart-count");
const cartTotalElement = document.getElementById("cart-total");
const messageElement = document.getElementById("message");
const paypalContainer = document.getElementById("paypal-button-container");
const requestForm = document.getElementById("request-form");
const requestInput = document.getElementById("shopping-request");
const analysisResult = document.getElementById("analysis-result");
const continueButton = document.getElementById("continue-recommendations");
const productDetails = document.getElementById("product-details");
const promiseDetails = document.getElementById("promise-details");
const guardianDetails = document.getElementById("guardian-details");
const evaluationDetails = document.getElementById("evaluation-details");

let currentRequest = "";
let currentRequirements = null;
let currentProduct = null;
let currentPromiseId = null;
let lastModal = null;

const modalIds = ["assistant-modal", "product-modal", "promise-modal", "guardian-modal", "evaluation-modal"];
const modal = id => document.getElementById(id);

function showMessage(text, type = "") {
    messageElement.textContent = text;
    messageElement.className = type;
}

async function readResponse(response) {
    const text = await response.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch {
        throw new Error(`Server returned an unexpected response (HTTP ${response.status}).`);
    }
    if (!response.ok) throw new Error(data.error || `Request failed (HTTP ${response.status}).`);
    return data;
}

function openModal(id) {
    modalIds.forEach(other => { if (other !== id) modal(other).hidden = true; });
    const element = modal(id);
    element.hidden = false;
    document.body.classList.add("modal-open");
    lastModal = document.activeElement;
    element.querySelector("button, input, textarea")?.focus();
}

function closeModal(element) {
    element.hidden = true;
    if (!modalIds.some(id => !modal(id).hidden)) {
        document.body.classList.remove("modal-open");
        lastModal?.focus();
    }
}

function productCard(product) {
    return `<article class="product-card">
        <img class="product-image" src="${product.image}" alt="${product.name}" onerror="this.onerror=null;this.src='data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%22700%22 height=%22350%22%3E%3Crect width=%22700%22 height=%22350%22 fill=%22%23e0e7ff%22/%3E%3C/svg%3E';">
        <div class="product-content"><span class="category">${product.category}</span><h3>${product.name}</h3>
        <p class="description">${product.description}</p><div class="product-footer"><span class="price">$${product.price} USD</span>
        <button type="button" data-product-id="${product.id}">View details</button></div></div></article>`;
}

function renderProducts(products) {
    catalogueElement.innerHTML = products.map(productCard).join("");
    catalogueElement.querySelectorAll("[data-product-id]").forEach(button => {
        button.addEventListener("click", () => selectProduct(button.dataset.productId));
    });
}

function renderCart(cart) {
    cartCountElement.textContent = `${cart.item_count} item${cart.item_count === 1 ? "" : "s"}`;
    cartTotalElement.textContent = `$${cart.total} ${cart.currency}`;
    if (!cart.items.length) {
        cartItemsElement.innerHTML = '<p class="empty">Your cart is empty.</p>';
        if (paypalContainer) paypalContainer.hidden = true;
        return;
    }
    cartItemsElement.innerHTML = cart.items.map(item => `<div class="cart-item">
        <div class="cart-item-head"><div><div class="cart-item-name">${item.product.name}</div><div class="cart-item-price">$${item.product.price} each</div></div><strong>$${item.subtotal}</strong></div>
        <div class="quantity-controls"><button type="button" data-action="update" data-product-id="${item.product.id}" data-quantity="${item.quantity - 1}" ${item.quantity <= 1 ? "disabled" : ""}>−</button><span class="quantity">${item.quantity}</span><button type="button" data-action="update" data-product-id="${item.product.id}" data-quantity="${item.quantity + 1}">+</button><button class="remove-button" type="button" data-action="remove" data-product-id="${item.product.id}">Remove</button></div>
    </div>`).join("");
    cartItemsElement.querySelectorAll("[data-action]").forEach(button => button.addEventListener("click", () => changeCart(button.dataset.action, button.dataset.productId, button.dataset.quantity)));
    if (paypalContainer) paypalContainer.hidden = false;
}

async function loadCart() { renderCart(await readResponse(await fetch("/api/cart"))); }

async function changeCart(action, productId, quantity = 1) {
    try {
        const response = await fetch("/api/cart", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({action, product_id:productId, quantity:Number(quantity)}) });
        renderCart(await readResponse(response));
        showMessage(action === "add" ? "Added to your cart." : "Cart updated.", "success");
    } catch (error) { showMessage(error.message, "error"); }
}

function renderRequirements(requirements, fallbackMessage = "") {
    const budget = requirements.budget ? `${requirements.budget.amount} ${requirements.budget.currency}` : "Unknown";
    analysisResult.innerHTML = `<div class="facts"><div class="fact">Budget<strong>${budget}</strong></div><div class="fact">Intended use<strong>${requirements.intended_use || "Unknown"}</strong></div><div class="fact">Category<strong>${requirements.category || "Unknown"}</strong></div></div>
        <p><strong>Required:</strong> ${(requirements.required_features || []).join(", ") || "None stated"}</p><p><strong>Preferred:</strong> ${(requirements.preferred_features || []).join(", ") || "None stated"}</p>${fallbackMessage ? `<p class="warning">${fallbackMessage}</p>` : ""}`;
    analysisResult.hidden = false;
    continueButton.hidden = false;
}

async function analyzeRequest(event) {
    event.preventDefault();
    currentRequest = requestInput.value.trim();
    if (!currentRequest) { showMessage("Describe what you need first.", "error"); return; }
    analysisResult.innerHTML = '<p class="loading-state">PayPilot is analyzing your request...</p>';
    analysisResult.hidden = false;
    try {
        const result = await readResponse(await fetch("/api/ai/analyze-requirements", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({request:currentRequest}) }));
        currentRequirements = result.requirements;
        renderRequirements(currentRequirements, result.ai_used ? "" : result.fallback_message);
    } catch (error) { analysisResult.innerHTML = `<p class="warning">${error.message}</p>`; }
}

async function selectProduct(productId) {
    try {
        const result = await readResponse(await fetch("/api/ai/explain-product", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({product_id:productId, request:currentRequest, requirements:currentRequirements}) }));
        currentProduct = result.product;
        productDetails.innerHTML = `<img class="product-image" src="${result.product.image}" alt="${result.product.name}"><div class="facts"><div class="fact">Price<strong>$${result.product.price} USD</strong></div><div class="fact">Match score<strong>${result.deterministic_match.match_score}</strong></div><div class="fact">Specs<strong>${Object.entries(result.product.specs).map(([key,value]) => `${key}: ${value}`).join(" · ")}</strong></div></div><p>${result.explanation.summary}</p><p><strong>Why:</strong> ${result.deterministic_match.reasons.join(" ") || "No verified match reason."}</p><p><strong>Trade-offs:</strong> ${result.explanation.tradeoffs.join(" ") || "None provided."}</p><div class="warning"><strong>Risks and unknowns:</strong> ${result.deterministic_match.risk_flags.join(" ")}</div>${result.ai_used ? "" : `<p class="muted">${result.fallback_message}</p>`}`;
        openModal("product-modal");
    } catch (error) { showMessage(error.message, "error"); }
}

async function createPromise() {
    try {
        const result = await readResponse(await fetch("/api/promises", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({original_request:currentRequest, product_id:currentProduct.id}) }));
        currentPromiseId = result.id;
        promiseDetails.innerHTML = `<p><strong>Request:</strong> ${result.original_request}</p><p><strong>Selection:</strong> ${result.product.name} — $${result.product.price} USD</p><p><strong>Requirements:</strong> ${result.requirements.primary_use || "Unknown"}; budget ${result.requirements.budget ? result.requirements.budget.amount + " " + result.requirements.budget.currency : "unknown"}.</p><p><strong>Verified specifications:</strong> ${Object.entries(result.product.specs).map(([key,value]) => `${key}: ${value}`).join(" · ")}</p><div class="warning">Warranty, repairability, live retailer data, and ratings are unknown. Any useful-lifetime value is a goal, not a guarantee.</div>`;
        closeModal(modal("product-modal")); openModal("promise-modal");
    } catch (error) { showMessage(error.message, "error"); }
}

async function approvePromise() {
    try {
        await readResponse(await fetch(`/api/promises/${currentPromiseId}/approve`, {method:"POST"}));
        closeModal(modal("promise-modal")); await loadCart(); showMessage("Purchase Promise approved. Continue with PayPal checkout.", "success");
        openModal("guardian-modal"); await loadGuardian();
    } catch (error) { showMessage(error.message, "error"); }
}

async function loadGuardian() {
    const result = await readResponse(await fetch(`/api/guardian/${currentPromiseId}`));
    guardianDetails.innerHTML = `<p class="warning">${result.monitoring_status}</p><p><strong>Original price:</strong> $${result.promise.product.price} USD</p><p><strong>Purchase status:</strong> ${result.promise.status}; payment ${result.promise.payment_status}</p><p><strong>Requirements:</strong> ${result.promise.requirements.primary_use || "Unknown"}</p><p><strong>Alerts:</strong> ${result.alerts.length ? result.alerts.map(alert => `${alert.message} (${alert.created_at})`).join("<br>") : "No observations recorded."}</p>`;
}

async function guardianCheck() {
    try { await readResponse(await fetch(`/api/guardian/${currentPromiseId}/check`, {method:"POST"})); await loadGuardian(); } catch (error) { showMessage(error.message, "error"); }
}

async function evaluatePurchase() {
    evaluationDetails.innerHTML = '<p class="loading-state">Evaluating the recorded purchase...</p>';
    openModal("evaluation-modal");
    try {
        const result = await readResponse(await fetch("/api/ai/evaluate-purchase", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({promise_id:currentPromiseId})}));
        const assessment = result.assessment;
        evaluationDetails.innerHTML = `<p>${assessment.assessment}</p><p><strong>Facts:</strong> ${assessment.facts.join(" ")}</p><p><strong>Unknowns:</strong> ${assessment.unknowns.join(" ")}</p><p><strong>Next steps:</strong> ${assessment.next_steps.join(" ")}</p>${result.ai_used ? "" : `<p class="muted">${result.fallback_message}</p>`}`;
    } catch (error) { evaluationDetails.innerHTML = `<p class="warning">${error.message}</p>`; }
}

async function setupPayPal() {
    if (!paypalContainer || !window.paypal?.Buttons) return;
    window.paypal.Buttons({
        async createOrder() { const data = await readResponse(await fetch("/api/create-order",{method:"POST"})); return data.id; },
        async onApprove(data) { try { const result = await readResponse(await fetch("/api/capture-order",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({orderID:data.orderID})})); if (result.status !== "COMPLETED") throw new Error("Payment was not confirmed."); showMessage(result.message,"success"); await loadCart(); } catch (error) { showMessage(`Payment was not confirmed: ${error.message}`,"error"); } },
        onCancel() { showMessage("Checkout cancelled. No completed payment was confirmed."); },
        onError(error) { console.error("PayPal checkout error:", error); showMessage(`PayPal checkout error: ${error.message || "Unknown error."}`,"error"); }
    }).render("#paypal-button-container");
}

document.querySelectorAll("[data-close-modal]").forEach(button => button.addEventListener("click", () => closeModal(button.closest(".modal-backdrop"))));
modalIds.forEach(id => modal(id).addEventListener("click", event => { if (event.target === modal(id)) closeModal(modal(id)); }));
document.addEventListener("keydown", event => { if (event.key === "Escape") { const open = modalIds.find(id => !modal(id).hidden); if (open) closeModal(modal(open)); } });
document.getElementById("open-assistant").addEventListener("click", () => openModal("assistant-modal"));
requestForm.addEventListener("submit", analyzeRequest);
continueButton.addEventListener("click", () => { closeModal(modal("assistant-modal")); showMessage("Choose a recommendation from the catalogue."); });
document.getElementById("create-promise").addEventListener("click", createPromise);
document.getElementById("approve-promise").addEventListener("click", approvePromise);
document.getElementById("guardian-check").addEventListener("click", guardianCheck);
document.getElementById("evaluate-purchase").addEventListener("click", evaluatePurchase);

(async function start() {
    try {
        const products = await readResponse(await fetch("/api/products"));
        renderProducts(products.products); await loadCart(); await setupPayPal();
    } catch (error) { showMessage(error.message, "error"); catalogueElement.innerHTML = '<p class="empty">Products could not be loaded.</p>'; }
})();
