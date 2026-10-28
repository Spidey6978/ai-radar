let currentTrack = "All";
let currentEvidenceTier = "All";
let currentCategory = "All";
let searchQuery = "";
let showOnlyBookmarked = false;
let isCompactMode = false;
let searchDebounceTimer = null;

document.addEventListener("DOMContentLoaded", () => {
    initTrackPills();
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
// FILTER PILLS & EVENT HANDLERS
// -------------------------------------------------------------

function initTrackPills() {
    const pills = document.querySelectorAll(".pill-track");
    pills.forEach(pill => {
        pill.addEventListener("click", () => {
            pills.forEach(p => p.classList.remove("active"));
            pill.classList.add("active");
            currentTrack = pill.getAttribute("data-track");
            fetchAndRenderFeed();
        });
    });
}

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
        }, 220);
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
// FEED RENDERING & QUERY-TIME FETCHING
// -------------------------------------------------------------

async function fetchAndRenderFeed() {
    const feedList = document.getElementById("feed-list");
    const countBadge = document.getElementById("intel-count");
    const bookmarkCountBadge = document.getElementById("bookmark-count");
    
    let url = `/api/feed?limit=70`;
    if (currentTrack && currentTrack !== "All") {
        url += `&track=${encodeURIComponent(currentTrack)}`;
    }
    if (currentEvidenceTier && currentEvidenceTier !== "All") {
        url += `&evidence_tier=${encodeURIComponent(currentEvidenceTier)}`;
    }
    if (currentCategory && currentCategory !== "All") {
        url += `&category=${encodeURIComponent(currentCategory)}`;
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
        if (bookmarkCountBadge && data.bookmark_count !== undefined) {
            bookmarkCountBadge.innerText = data.bookmark_count;
        }

        if (!data.items || data.items.length === 0) {
            feedList.innerHTML = `
                <div class="intel-card" style="text-align: center; color: var(--dim); padding: 48px;">
                    No intel signals match the active filters. Try clearing search or switching tracks.
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
    const clusterBadge = (item.cluster_size && item.cluster_size > 1) ?
        `<span class="tag tag-cluster" title="Corroborated across multiple independent sources">⚡ ${item.cluster_size} Sources Corroborating</span>` : "";

    const isBookmarked = item.is_bookmarked === 1;
    const bookmarkLabel = isBookmarked ? "🔖 Saved" : "🔖 Save";
    const bookmarkClass = isBookmarked ? "active" : "";
    const isReadClass = item.is_read ? "is-read" : "";

    let discussionsHtml = "";
    if (item.discussion_urls && Array.isArray(item.discussion_urls)) {
        item.discussion_urls.forEach(d => {
            if (d && d !== item.canonical_url) {
                discussionsHtml += `<a href="${escapeHtml(d)}" target="_blank" rel="noopener" class="discussion-link">💬 Discussion</a>`;
            }
        });
    }

    let diffHtml = "";
    if (item.evidence_diff) {
        diffHtml = `
            <div class="card-diff-container">
                <div class="diff-header font-mono">AUTOMATED SNAPSHOT DIFF</div>
                <pre class="diff-content font-mono">${escapeHtml(item.evidence_diff)}</pre>
            </div>
        `;
    }

    let coverageHtml = "";
    if (item.coverage_sources && Array.isArray(item.coverage_sources) && item.coverage_sources.length > 1) {
        let pills = item.coverage_sources.map(cov => 
            `<a href="${escapeHtml(cov.url)}" target="_blank" rel="noopener" class="coverage-pill" title="${escapeHtml(cov.title)}">${escapeHtml(cov.source_name)} ↗</a>`
        ).join("");
        coverageHtml = `
            <div class="card-coverage-bar">
                <span class="coverage-label font-mono">CORROBORATION:</span>
                <div class="coverage-pills">${pills}</div>
            </div>
        `;
    }

    return `
        <article class="intel-card ${isReadClass}" id="card-${item.id}" data-id="${item.id}">
            <div class="card-meta">
                ${evidenceBadge}
                <span class="tag tag-category">${escapeHtml(item.category)}</span>
                <span class="tag tag-source">${escapeHtml(item.source_name)}</span>
                ${scoreBadge}
                ${clusterBadge}
                <span class="card-time font-mono">${formatDate(item.published_at || item.created_at)}</span>
            </div>

            <h2 class="card-title">
                <a href="${escapeHtml(item.canonical_url)}" target="_blank" rel="noopener" class="card-title-link">${escapeHtml(item.title)}</a>
            </h2>

            ${item.clean_summary ? `<p class="card-summary">${escapeHtml(item.clean_summary)}</p>` : ""}

            ${diffHtml}
            ${coverageHtml}

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
// USER ACTIONS (READ, BOOKMARK, DISMISS, COPY)
// -------------------------------------------------------------

function bindCardActions(container) {
    container.querySelectorAll(".card-title-link").forEach(link => {
        link.addEventListener("click", () => {
            const card = link.closest(".intel-card");
            if (!card) return;
            const itemId = card.getAttribute("data-id");
            card.classList.add("is-read");
            postItemAction(itemId, { is_read: true });
        });
    });

    container.querySelectorAll(".btn-bookmark").forEach(btn => {
        btn.addEventListener("click", async () => {
            const itemId = btn.getAttribute("data-id");
            const currentlySaved = btn.classList.contains("active");
            const newSaved = !currentlySaved;
            
            btn.classList.toggle("active", newSaved);
            btn.innerText = newSaved ? "🔖 Saved" : "🔖 Save";
            
            const res = await postItemAction(itemId, { is_bookmarked: newSaved });
            if (res && res.bookmark_count !== undefined) {
                const badge = document.getElementById("bookmark-count");
                if (badge) badge.innerText = res.bookmark_count;
            }
        });
    });

    container.querySelectorAll(".btn-dismiss").forEach(btn => {
        btn.addEventListener("click", async () => {
            const itemId = btn.getAttribute("data-id");
            const card = document.getElementById(`card-${itemId}`);
            if (card) {
                card.style.opacity = "0";
                card.style.transform = "scale(0.98)";
                setTimeout(() => card.remove(), 160);
            }
            await postItemAction(itemId, { is_dismissed: true });
        });
    });

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
                btn.style.color = "var(--verified)";
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
        const res = await fetch(`/api/feed/${itemId}/action`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        return await res.json();
    } catch (err) {
        console.error("Failed to update item action:", err);
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
            
            setTimeout(async () => {
                await fetchAndRenderFeed();
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
