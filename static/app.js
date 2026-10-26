let currentCategory = "All";
let searchQuery = "";
let searchDebounceTimer = null;

document.addEventListener("DOMContentLoaded", () => {
    initCategoryPills();
    initSearch();
    initRefreshButton();
    initFeedbackForm();
});

function initCategoryPills() {
    const pills = document.querySelectorAll(".pill");
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
        }, 300);
    });
}

async function fetchAndRenderFeed() {
    const feedList = document.getElementById("feed-list");
    const countBadge = document.getElementById("intel-count");
    
    let url = `/api/feed?limit=80`;
    if (currentCategory && currentCategory !== "All") {
        url += `&category=${encodeURIComponent(currentCategory)}`;
    }
    if (searchQuery) {
        url += `&search=${encodeURIComponent(searchQuery)}`;
    }

    try {
        const res = await fetch(url);
        const data = await res.json();
        
        if (countBadge) {
            countBadge.innerText = `${data.count} INTEL SIGNALS`;
        }

        if (!data.articles || data.articles.length === 0) {
            feedList.innerHTML = `
                <div class="intel-card" style="text-align: center; color: var(--text-dim); padding: 32px;">
                    No intel matches this filter right now. Try scanning channels or searching another keyword.
                </div>
            `;
            return;
        }

        feedList.innerHTML = data.articles.map(art => renderCard(art)).join("");
    } catch (err) {
        console.error("Failed to load feed:", err);
    }
}

function getCredibilityClass(cred) {
    if (cred.includes("Confirmed")) return "badge-green";
    if (cred.includes("Credible") || cred.includes("Developing") || cred.includes("Community")) return "badge-yellow";
    return "badge-red";
}

function renderCard(art) {
    const credClass = getCredibilityClass(art.credibility || "");
    const discussionLink = art.discussion_url ? 
        `<a href="${art.discussion_url}" target="_blank" rel="noopener" class="discussion-link">💬 Community Discussion</a>` : "";

    return `
        <article class="intel-card">
            <div class="card-meta">
                <span class="tag ${credClass}">${escapeHtml(art.credibility || "Unverified")}</span>
                <span class="tag tag-category">${escapeHtml(art.category)}</span>
                <span class="tag tag-source">${escapeHtml(art.source)}</span>
                <span class="card-time">${formatDate(art.published_at || art.created_at)}</span>
            </div>
            <h2 class="card-title">
                <a href="${escapeHtml(art.url)}" target="_blank" rel="noopener">${escapeHtml(art.title)}</a>
            </h2>
            <p class="card-summary">${escapeHtml(art.summary || "")}</p>
            <div class="card-footer">
                <a href="${escapeHtml(art.url)}" target="_blank" rel="noopener" class="card-link">Primary Source ↗</a>
                ${discussionLink}
            </div>
        </article>
    `;
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

function initRefreshButton() {
    const refreshBtn = document.getElementById("btn-refresh");
    if (!refreshBtn) return;

    refreshBtn.addEventListener("click", async () => {
        refreshBtn.disabled = true;
        refreshBtn.innerHTML = `<span class="loading-spinner"></span> Scanning...`;

        try {
            const res = await fetch("/api/refresh", { method: "POST" });
            const data = await res.json();
            
            // Poll feed stats until fetching completes
            let attempts = 0;
            const pollInterval = setInterval(async () => {
                attempts++;
                const statsRes = await fetch("/api/stats");
                const stats = await statsRes.json();
                
                if (!stats.is_fetching || attempts > 15) {
                    clearInterval(pollInterval);
                    refreshBtn.disabled = false;
                    refreshBtn.innerHTML = `⟳ Scan Channels`;
                    fetchAndRenderFeed();
                    const statusText = document.getElementById("last-updated");
                    if (statusText && stats.last_updated) {
                        statusText.innerText = `Updated ${stats.last_updated}`;
                    }
                }
            }, 1500);
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
                statusBox.innerHTML = `<span class="status-success">✓ Received! We'll factor this topic into the radar pipeline.</span>`;
                form.reset();
            } else {
                statusBox.innerHTML = `<span class="status-error">${data.error || "Submission failed"}</span>`;
            }
        } catch (err) {
            statusBox.innerHTML = `<span class="status-error">Network error. Please try again.</span>`;
        } finally {
            submitBtn.disabled = false;
            submitBtn.innerText = "Submit Suggestion";
        }
    });
}
