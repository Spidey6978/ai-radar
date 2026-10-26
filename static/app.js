let currentCategory = "All";
let currentEvidenceTier = "All";
let searchQuery = "";
let showOnlyBookmarked = false;
let isCompactMode = false;
let searchDebounceTimer = null;

// Local bookmark cache for immediate UI feedback
const bookmarkedIds = new Set();

document.addEventListener("DOMContentLoaded", () => {
    initEvidencePills();
    initCategoryPills();
    initSearch();
    initToggles();
    initHealthDrawer();
    initRefreshButton();
    initFeedbackForm();
    bindCardActions(document);
});

// -------------------------------------------------------------
// FILTER PILLS
// -------------------------------------------------------------

function initEvidencePills() {
    const pills = document.querySelectorAll(".pill-evidence");
    pills.forEach(pill => {
        pill.addEventListener("click", () => {
            pills.forEach(p => p.classList.remove("active"));
            pill.classList.add("active");
            currentEvidenceTier = pill.getAttribute("data-evidence");
            fetchAndRenderFeed();
        });
    });
}

function initCategoryPills() {
    const pills = document.querySelectorAll(".pill-category");
    pills.forEach(pill => {
        pill.addEventListener("click", () => {
            pills.forEach(p => p.classList.remove("active"));
            pill.classList.add("active");
            currentCategory = pill.getAttribute("data-category");
            fetchAndRenderFeed();
        });
    });
}

function initSearch() {
    const searchInput = document.getElementById("search-input");
    if (!searchInput) return;

    searchInput.addEventListener("input", (e) => {
        clearTimeout(searchDebounceTimer);
        searchQuery = e.target.value.trim();
        searchDebounceTimer = setTimeout(() => {
            fetchAndRenderFeed();
        }, 250);
    });
}

function initToggles() {
    const bookmarkBtn = document.getElementById("btn-bookmarked");
    if (bookmarkBtn) {
        bookmarkBtn.addEventListener("click", () => {
            showOnlyBookmarked = !showOnlyBookmarked;
            bookmarkBtn.classList.toggle("active", showOnlyBookmarked);
            fetchAndRenderFeed();
        });
    }

    const densityBtn = document.getElementById("btn-density");
    const feedList = document.getElementById("feed-list");
    if (densityBtn && feedList) {
        densityBtn.addEventListener("click", () => {
            isCompactMode = !isCompactMode;
            feedList.classList.toggle("feed-compact", isCompactMode);
            densityBtn.classList.toggle("active", isCompactMode);
            densityBtn.innerText = isCompactMode ? "≡ Expanded View" : "☰ Compact View";
        });
    }
}

function initHealthDrawer() {
    const modalBtn = document.getElementById("btn-health-modal");
    const closeBtn = document.getElementById("btn-close-health");
    const drawer = document.getElementById("health-drawer");

    if (modalBtn && drawer) {
        modalBtn.addEventListener("click", () => {
            drawer.classList.toggle("hidden");
        });
    }
    if (closeBtn && drawer) {
        closeBtn.addEventListener("click", () => {
            drawer.classList.add("hidden");
        });
    }
}

// -------------------------------------------------------------
// FEED RENDERING & FETCHING
// -------------------------------------------------------------

async function fetchAndRenderFeed() {
    const feedList = document.getElementById("feed-list");
    const countBadge = document.getElementById("intel-count");
    
    let url = `/api/feed?limit=70`;
    if (currentCategory && currentCategory !== "All") {
        url += `&category=${encodeURIComponent(currentCategory)}`;
    }
    if (currentEvidenceTier && currentEvidenceTier !== "All") {
        url += `&evidence_tier=${encodeURIComponent(currentEvidenceTier)}`;
    }
    if (searchQuery) {
        url += `&search=${encodeURIComponent(searchQuery)}`;
    }
    if (showOnlyBookmarked) {
        url += `&only_bookmarked=true`;
    }

    try {
        const res = await fetch(url);
        const data = await res.json();
        
        if (countBadge) {
            countBadge.innerText = `${data.count} INTEL SIGNALS`;
        }

        if (!data.items || data.items.length === 0) {
            feedList.innerHTML = `
                <div class="intel-card" style="text-align: center; color: var(--text-dim); padding: 40px;">
                    No intel signals match the active filters. Try resetting search or checking another evidence tier.
                </div>
            `;
            return;
        }

        feedList.innerHTML = data.items.map(item => renderCard(item)).join("");
        bindCardActions(feedList);
    } catch (err) {
        console.error("Failed to load feed:", err);
    }
}

function getEvidenceBadge(tier) {
    switch (tier) {
        case "verified_artifact":
            return `<span class="tag badge-green">🟢 Verified Artifact</span>`;
        case "technical_paper":
            return `<span class="tag badge-yellow">🟡 Research Paper</span>`;
        case "security_advisory":
            return `<span class="tag badge-blue">🔵 Security Advisory</span>`;
        case "community_claim":
            return `<span class="tag badge-muted">⚪ Community Lead</span>`;
        default:
            return `<span class="tag badge-secondary">📰 Secondary Press</span>`;
    }
}

function renderCard(item) {
    const evidenceBadge = getEvidenceBadge(item.evidence_tier);
    const scoreBadge = item.community_score > 0 ? 
        `<span class="tag tag-score">🔥 ${item.community_score} pts</span>` : "";

    const isBookmarked = item.is_bookmarked || bookmarkedIds.has(item.id);
    const bookmarkLabel = isBookmarked ? "🔖 Saved" : "🔖 Save";
    const bookmarkClass = isBookmarked ? "active" : "";
    const isReadClass = item.is_read ? "is-read" : "";

    let discussionsHtml = "";
    if (item.discussion_urls && Array.isArray(item.discussion_urls)) {
        item.discussion_urls.forEach(d => {
            if (d && d !== item.canonical_url) {
                discussionsHtml += `<a href="${escapeHtml(d)}" target="_blank" rel="noopener" class="discussion-link">💬 Community Discussion</a>`;
            }
        });
    }

    return `
        <article class="intel-card ${isReadClass}" id="card-${item.id}" data-id="${item.id}">
            <div class="card-meta">
                ${evidenceBadge}
                <span class="tag tag-category">${escapeHtml(item.category)}</span>
                <span class="tag tag-source">${escapeHtml(item.source_name)}</span>
                ${scoreBadge}
                <span class="card-time font-mono">${formatDate(item.published_at || item.created_at)}</span>
            </div>

            <h2 class="card-title">
                <a href="${escapeHtml(item.canonical_url)}" target="_blank" rel="noopener" class="card-title-link">${escapeHtml(item.title)}</a>
            </h2>

            ${item.clean_summary ? `<p class="card-summary">${escapeHtml(item.clean_summary)}</p>` : ""}

            <div class="card-footer">
                <div class="footer-links">
                    <a href="${escapeHtml(item.canonical_url)}" target="_blank" rel="noopener" class="card-link">Primary Source ↗</a>
                    ${discussionsHtml}
                </div>

                <div class="card-actions">
                    <button class="btn-action btn-copy-tldr" data-id="${item.id}" title="Copy TL;DR markdown to clipboard">
                        📋 Copy
                    </button>
                    <button class="btn-action btn-bookmark ${bookmarkClass}" data-id="${item.id}" title="Bookmark this item">
                        ${bookmarkLabel}
                    </button>
                    <button class="btn-action btn-dismiss" data-id="${item.id}" title="Dismiss from feed">
                        ✕ Dismiss
                    </button>
                </div>
            </div>
        </article>
    `;
}

// -------------------------------------------------------------
// CARD ACTIONS (READ, BOOKMARK, DISMISS, COPY)
// -------------------------------------------------------------

function bindCardActions(container) {
    // Title click -> mark read
    container.querySelectorAll(".card-title-link").forEach(link => {
        link.addEventListener("click", (e) => {
            const card = link.closest(".intel-card");
            if (!card) return;
            const itemId = card.getAttribute("data-id");
            card.classList.add("is-read");
            postItemAction(itemId, { is_read: true });
        });
    });

    // Bookmark button
    container.querySelectorAll(".btn-bookmark").forEach(btn => {
        btn.addEventListener("click", async () => {
            const itemId = btn.getAttribute("data-id");
            const currentlySaved = btn.classList.contains("active");
            const newSaved = !currentlySaved;
            
            btn.classList.toggle("active", newSaved);
            btn.innerText = newSaved ? "🔖 Saved" : "🔖 Save";
            
            if (newSaved) {
                bookmarkedIds.add(itemId);
            } else {
                bookmarkedIds.delete(itemId);
            }
            updateBookmarkCount();
            await postItemAction(itemId, { is_bookmarked: newSaved });
        });
    });

    // Dismiss button
    container.querySelectorAll(".btn-dismiss").forEach(btn => {
        btn.addEventListener("click", async () => {
            const itemId = btn.getAttribute("data-id");
            const card = document.getElementById(`card-${itemId}`);
            if (card) {
                card.style.opacity = "0";
                card.style.transform = "scale(0.98)";
                setTimeout(() => card.remove(), 150);
            }
            await postItemAction(itemId, { is_dismissed: true });
        });
    });

    // Copy TL;DR button
    container.querySelectorAll(".btn-copy-tldr").forEach(btn => {
        btn.addEventListener("click", () => {
            const card = btn.closest(".intel-card");
            if (!card) return;
            const title = card.querySelector(".card-title a")?.innerText || "";
            const summary = card.querySelector(".card-summary")?.innerText || "";
            const url = card.querySelector(".card-link")?.getAttribute("href") || "";

            const textToCopy = `**[AI Radar Intel] ${title}**\n${summary}\n🔗 ${url}`;
            navigator.clipboard.writeText(textToCopy).then(() => {
                const originalText = btn.innerText;
                btn.innerText = "✓ Copied!";
                btn.style.color = "var(--accent-green)";
                setTimeout(() => {
                    btn.innerText = originalText;
                    btn.style.color = "";
                }, 1400);
            });
        });
    });
}

async function postItemAction(itemId, payload) {
    try {
        await fetch(`/api/feed/${itemId}/action`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
    } catch (err) {
        console.error("Failed to update item action:", err);
    }
}

function updateBookmarkCount() {
    const countEl = document.getElementById("bookmark-count");
    if (countEl) {
        countEl.innerText = bookmarkedIds.size;
    }
}

function formatDate(isoStr) {
    if (!isoStr) return "";
    try {
        const d = new Date(isoStr);
        if (isNaN(d.getTime())) return isoStr.slice(0, 16);
        const diffMs = Date.now() - d.getTime();
        const diffHrs = Math.floor(diffMs / (1000 * 60 * 60));
        
        if (diffHrs < 1) {
            const diffMins = Math.max(1, Math.floor(diffMs / (1000 * 60)));
            return `${diffMins}m ago`;
        }
        if (diffHrs < 24) return `${diffHrs}h ago`;
        const diffDays = Math.floor(diffHrs / 24);
        return `${diffDays}d ago`;
    } catch {
        return "";
    }
}

function escapeHtml(str) {
    if (!str) return "";
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

// -------------------------------------------------------------
// REFRESH & FEEDBACK
// -------------------------------------------------------------

function initRefreshButton() {
    const refreshBtn = document.getElementById("btn-refresh");
    if (!refreshBtn) return;

    refreshBtn.addEventListener("click", async () => {
        refreshBtn.disabled = true;
        refreshBtn.innerHTML = `⟳ Scanning...`;

        try {
            await fetch("/api/collector/trigger", { method: "POST" });
            
            // Wait 4 seconds for adapters to complete background cycle, then refresh feed
            setTimeout(async () => {
                await fetchAndRenderFeed();
                // Update sources health badge
                const healthRes = await fetch("/api/sources/health");
                const healthData = await healthRes.json();
                const summaryEl = document.getElementById("sources-summary");
                if (summaryEl) {
                    summaryEl.innerText = `${healthData.active_sources}/${healthData.total_sources} Sources Active`;
                }
                refreshBtn.disabled = false;
                refreshBtn.innerHTML = `⟳ Scan Channels`;
            }, 4500);
        } catch (err) {
            console.error("Refresh error:", err);
            refreshBtn.disabled = false;
            refreshBtn.innerHTML = `⟳ Scan Channels`;
        }
    });
}

function initFeedbackForm() {
    const form = document.getElementById("feedback-form");
    const statusBox = document.getElementById("feedback-status");
    if (!form) return;

    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const suggestion = document.getElementById("feedback-suggestion").value.trim();
        const willingToPay = document.getElementById("feedback-pay").value;
        const contact = document.getElementById("feedback-contact").value.trim();

        if (!suggestion) {
            statusBox.innerHTML = `<span class="status-error">Please enter a suggestion or topic to track.</span>`;
            return;
        }

        const submitBtn = form.querySelector("button[type='submit']");
        submitBtn.disabled = true;
        submitBtn.innerText = "Submitting...";

        try {
            const res = await fetch("/api/feedback", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    suggestion: suggestion,
                    willing_to_pay: willingToPay,
                    contact: contact
                })
            });
            const data = await res.json();

            if (res.ok) {
                statusBox.innerHTML = `<span class="status-success">✓ Received! Topic prioritized for radar tracking.</span>`;
                form.reset();
            } else {
                statusBox.innerHTML = `<span class="status-error">${data.detail || "Submission failed"}</span>`;
            }
        } catch (err) {
            statusBox.innerHTML = `<span class="status-error">Network error. Please try again.</span>`;
        } finally {
            submitBtn.disabled = false;
            submitBtn.innerText = "Submit Suggestion";
        }
    });
}
