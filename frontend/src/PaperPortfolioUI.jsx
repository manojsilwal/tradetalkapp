import React, { useState, useEffect, useCallback } from "react";
import { useLocation } from "react-router-dom";
import { Plus, Target, ImageUp, X } from "lucide-react";
import { API_BASE_URL, apiFetch, apiPostMultipart, fetchJsonWithMeta } from "./api";

const fmt = (n, dec = 2) => (n >= 0 ? "+" : "") + n.toFixed(dec);
const fmtUSD = (n) => (n >= 0 ? "+$" : "-$") + Math.abs(n).toFixed(2);
const fmtPlainUSD = (n) => `$${Number(n || 0).toFixed(2)}`;

function topBreakdown(map = {}) {
  const entries = Object.entries(map).sort((a, b) => Number(b[1]) - Number(a[1]));
  return entries.slice(0, 3);
}

function rowStatus(ticker, rec) {
  if (!rec || !ticker) return "—";
  if (rec.new?.some((x) => x.ticker === ticker)) return "New";
  if (rec.updated?.some((x) => x.ticker === ticker)) return "Updated";
  if (rec.unchanged?.some((x) => x.ticker === ticker)) return "Unchanged";
  return "—";
}

export default function PaperPortfolioUI({ onXpGained }) {
  const location = useLocation();
  const [perf, setPerf] = useState(null);
  const [perfLoading, setPerfLoading] = useState(true);
  const [perfError, setPerfError] = useState(null);
  const [showAdd, setShowAdd] = useState(false);
  const [addForm, setAddForm] = useState({
    ticker: "",
    direction: "LONG",
    price: "",
    shares: "",
    note: "",
  });
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState("");
  const [closing, setClosing] = useState(null);

  const [importExpanded, setImportExpanded] = useState(false);
  const [fullSnapshot, setFullSnapshot] = useState(false);
  const [importBusy, setImportBusy] = useState(false);
  const [importStatus, setImportStatus] = useState("");
  const [importErr, setImportErr] = useState("");
  const [reviewOpen, setReviewOpen] = useState(false);
  const [reviewRows, setReviewRows] = useState([]);
  const [reconciliation, setReconciliation] = useState(null);
  const [manualDraft, setManualDraft] = useState([
    { ticker: "", shares: "", avg_cost: "" },
  ]);

  const openReview = useCallback((payload) => {
    const rows = (payload.holdings || []).map((h) => ({
      ticker: h.ticker || "",
      shares: h.shares != null ? String(h.shares) : "",
      avg_cost: h.avg_cost != null ? String(h.avg_cost) : "",
    }));
    setReviewRows(rows.length ? rows : [{ ticker: "", shares: "", avg_cost: "" }]);
    setReconciliation(payload.reconciliation || null);
    setReviewOpen(true);
    setImportErr("");
  }, []);

  const runPreviewManual = async () => {
    const items = manualDraft
      .map((r) => ({
        ticker: (r.ticker || "").trim().toUpperCase(),
        shares: r.shares === "" ? null : Number(r.shares),
        avg_cost: r.avg_cost === "" ? null : Number(r.avg_cost),
      }))
      .filter((r) => r.ticker);
    if (!items.length) {
      setImportErr("Add at least one ticker to preview.");
      return;
    }
    setImportBusy(true);
    setImportErr("");
    try {
      const data = await apiFetch(`${API_BASE_URL}/portfolio/preview-holdings-import`, {
        method: "POST",
        body: JSON.stringify({ items, full_snapshot: fullSnapshot }),
      });
      openReview(data);
    } catch (e) {
      setImportErr(e.message || "Preview failed");
    } finally {
      setImportBusy(false);
    }
  };

  const runParseImage = async (fileList) => {
    const files = Array.from(fileList || []).filter(
      (f) => f && (f.type || "").startsWith("image/")
    );
    if (!files.length) {
      setImportErr("Choose at least one image.");
      return;
    }
    if (files.length > 10) {
      setImportErr("Maximum 10 images per upload.");
      return;
    }
    setImportBusy(true);
    setImportErr("");
    setImportStatus(
      `Parsing ${files.length} screenshot${files.length > 1 ? "s" : ""} with Gemini…`
    );
    try {
      const fd = new FormData();
      files.forEach((f) => fd.append("files", f));
      fd.append("full_snapshot", fullSnapshot ? "true" : "false");
      const data = await apiPostMultipart(
        `${API_BASE_URL}/portfolio/parse-holdings-image`,
        fd,
        Math.min(180000, 45000 + files.length * 30000)
      );
      openReview(data);
      if (data.parse_warnings?.length) {
        setImportErr(
          `Parsed ${data.images_parsed ?? files.length} of ${files.length} image(s). ` +
            data.parse_warnings.join(" · ")
        );
      }
    } catch (e) {
      setImportErr(e.message || "Could not parse screenshots");
    } finally {
      setImportBusy(false);
      setImportStatus("");
    }
  };

  const applyReview = async () => {
    const items = reviewRows
      .map((r) => ({
        ticker: (r.ticker || "").trim().toUpperCase(),
        shares: r.shares === "" ? null : Number(r.shares),
        avg_cost: r.avg_cost === "" ? null : Number(r.avg_cost),
      }))
      .filter((r) => r.ticker && r.shares != null && r.shares > 0);
    if (!items.length) {
      setImportErr("Need at least one row with ticker and positive shares.");
      return;
    }
    setImportBusy(true);
    setImportErr("");
    try {
      await apiFetch(`${API_BASE_URL}/portfolio/apply-holdings-import`, {
        method: "POST",
        body: JSON.stringify({
          items,
          full_snapshot: fullSnapshot,
          source: "holdings_import",
          note: "",
        }),
      });
      setReviewOpen(false);
      setImportExpanded(false);
      await fetchPerf();
      if (onXpGained) onXpGained({ xp_awarded: 10, new_badges: [] });
    } catch (e) {
      setImportErr(e.message || "Apply failed");
    } finally {
      setImportBusy(false);
    }
  };

  useEffect(() => {
    if (location.state?.addTicker) {
      setAddForm((f) => ({ ...f, ticker: location.state.addTicker }));
      setShowAdd(true);
    }
  }, [location.state]);

  const fetchPerf = async () => {
    setPerfLoading(true);
    setPerfError(null);
    try {
      const { data } = await fetchJsonWithMeta(
        `${API_BASE_URL}/portfolio/performance`,
        {},
        20000
      );
      setPerf(data);
    } catch (e) {
      setPerfError(
        e.message ||
          "Could not load portfolio performance. Start the API on port 8000 or check VITE_API_BASE_URL."
      );
    } finally {
      setPerfLoading(false);
    }
  };

  useEffect(() => {
    fetchPerf();
  }, []);

  const handleAdd = async () => {
    if (!addForm.ticker.trim()) {
      setAddError("Enter a ticker");
      return;
    }
    if (!Number(addForm.price) || Number(addForm.price) <= 0) {
      setAddError("Enter a positive purchase price");
      return;
    }
    if (!Number(addForm.shares) || Number(addForm.shares) <= 0) {
      setAddError("Enter a positive share count");
      return;
    }
    setAdding(true);
    setAddError("");
    try {
      const data = await apiFetch(`${API_BASE_URL}/portfolio/position`, {
        method: "POST",
        body: JSON.stringify({
          ...addForm,
          price: Number(addForm.price),
          shares: Number(addForm.shares),
          source: "manual_price_shares",
        }),
      });
      if (data.error) {
        setAddError(data.error);
        return;
      }
      if (onXpGained) onXpGained({ xp_awarded: 10, new_badges: [] });
      setShowAdd(false);
      setAddForm({ ticker: "", direction: "LONG", price: "", shares: "", note: "" });
      await fetchPerf();
    } catch {
      setAddError("Failed to add position");
    } finally {
      setAdding(false);
    }
  };

  const handleClose = async (posId) => {
    setClosing(posId);
    try {
      await apiFetch(`${API_BASE_URL}/portfolio/close/${posId}`, {
        method: "POST",
      });
      await fetchPerf();
    } finally {
      setClosing(null);
    }
  };

  const positions = perf?.positions || [];
  const beatingSPY = perf?.beating_spy;
  const analysis = perf?.analysis || {};

  return (
    <div style={{ maxWidth: 920, margin: "0 auto", padding: "0 16px" }}>
      <div style={{ marginBottom: 22 }}>
        <div style={{ fontSize: 11, color: "#a78bfa", fontWeight: 800, letterSpacing: 1.2, marginBottom: 6 }}>
          PORTFOLIO BUILDER
        </div>
        <h2 style={{ margin: 0, color: "#f8fafc", fontSize: 28 }}>
          Add stocks manually or import a broker screenshot
        </h2>
        <p style={{ margin: "8px 0 0", color: "#94a3b8", fontSize: 13, lineHeight: 1.6 }}>
          Each holding is automatically tagged by sector, asset type, and market-cap bucket for later portfolio analysis.
        </p>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
          gap: 14,
          marginBottom: 24,
        }}
      >
        <button
          type="button"
          onClick={() => {
            setShowAdd(true);
            setImportExpanded(false);
          }}
          style={optionCardStyle(showAdd)}
        >
          <Plus size={22} />
          <span style={{ fontWeight: 800, fontSize: 15 }}>Add stock one by one</span>
          <span style={{ color: "#94a3b8", fontSize: 12, lineHeight: 1.5 }}>
            Enter ticker, buy price, and number of shares. TradeTalk tags the position automatically.
          </span>
        </button>
        <button
          type="button"
          onClick={() => {
            setImportExpanded(true);
            setShowAdd(false);
          }}
          style={optionCardStyle(importExpanded)}
        >
          <ImageUp size={22} />
          <span style={{ fontWeight: 800, fontSize: 15 }}>Upload broker screenshot</span>
          <span style={{ color: "#94a3b8", fontSize: 12, lineHeight: 1.5 }}>
            Bulk import from multiple broker screenshots at once using Gemini 3.5 Flash vision.
          </span>
        </button>
      </div>

      {perfError ? (
        <div
          style={{
            marginBottom: 18,
            padding: "12px 14px",
            borderRadius: 12,
            border: "1px solid rgba(239,68,68,0.35)",
            background: "rgba(239,68,68,0.08)",
            color: "#fca5a5",
            fontSize: 13,
            display: "flex",
            flexWrap: "wrap",
            gap: 10,
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <span style={{ flex: "1 1 240px", lineHeight: 1.5 }}>{perfError}</span>
          <button type="button" onClick={fetchPerf} style={btnStyle("#7c3aed")}>
            Retry
          </button>
        </div>
      ) : null}

      {/* Portfolio Summary */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, 1fr)",
          gap: 14,
          marginBottom: 24,
        }}
      >
        {perfLoading
          ? [1, 2, 3].map((i) => (
              <div
                key={i}
                style={{
                  background: "rgba(255,255,255,0.04)",
                  border: "1px solid rgba(255,255,255,0.08)",
                  borderRadius: 14,
                  padding: "16px 18px",
                  minHeight: 88,
                  opacity: 0.6,
                }}
              />
            ))
          : [
              {
                label: "Portfolio Value",
                value: `$${(perf?.total_value || 0).toFixed(2)}`,
                sub: `Started $${perf?.starting_cash?.toFixed(0) || "10,000"}`,
                color: "#a78bfa",
              },
              {
                label: "Total P&L",
                value: fmtUSD(perf?.total_pnl || 0),
                sub: `${fmt(perf?.total_pnl_pct || 0)}%`,
                color: (perf?.total_pnl || 0) >= 0 ? "#10b981" : "#ef4444",
              },
              {
                label: "vs SPY",
                value:
                  perf?.spy_pnl_pct != null
                    ? `${fmt(perf?.total_pnl_pct || 0)}% vs ${fmt(perf.spy_pnl_pct)}%`
                    : `${fmt(perf?.total_pnl_pct || 0)}% · SPY n/a`,
                sub:
                  perf?.spy_pnl_pct != null
                    ? beatingSPY
                      ? "Beating the market"
                      : "SPY is ahead"
                    : "Could not load SPY benchmark",
                color:
                  perf?.spy_pnl_pct != null
                    ? beatingSPY
                      ? "#10b981"
                      : "#f59e0b"
                    : "#94a3b8",
              },
            ].map((card) => (
              <div
                key={card.label}
                style={{
                  background: "rgba(255,255,255,0.04)",
                  border: "1px solid rgba(255,255,255,0.08)",
                  borderRadius: 14,
                  padding: "16px 18px",
                }}
              >
                <div
                  style={{
                    fontSize: 10,
                    color: "#64748b",
                    fontWeight: 600,
                    letterSpacing: 1,
                    marginBottom: 8,
                  }}
                >
                  {card.label.toUpperCase()}
                </div>
                <div
                  style={{
                    fontSize: 20,
                    fontWeight: 800,
                    color: card.color,
                    marginBottom: 4,
                  }}
                >
                  {card.value}
                </div>
                <div style={{ fontSize: 11, color: "#64748b" }}>{card.sub}</div>
              </div>
            ))}
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
          gap: 12,
          marginBottom: 22,
        }}
      >
        {[
          ["Sector mix", analysis.by_sector],
          ["Market-cap mix", analysis.by_cap_bucket],
          ["Asset type", analysis.by_asset_type],
        ].map(([label, data]) => {
          const rows = topBreakdown(data);
          return (
            <div
              key={label}
              style={{
                background: "rgba(255,255,255,0.03)",
                border: "1px solid rgba(255,255,255,0.08)",
                borderRadius: 14,
                padding: "14px 16px",
                minHeight: 92,
              }}
            >
              <div style={{ fontSize: 10, color: "#64748b", fontWeight: 700, letterSpacing: 1, marginBottom: 10 }}>
                {label.toUpperCase()}
              </div>
              {rows.length ? rows.map(([name, value]) => (
                <div key={name} style={{ display: "flex", justifyContent: "space-between", gap: 10, fontSize: 12, color: "#cbd5e1", marginBottom: 6 }}>
                  <span>{name}</span>
                  <strong>{fmtPlainUSD(value)}</strong>
                </div>
              )) : (
                <div style={{ color: "#64748b", fontSize: 12 }}>Add holdings to unlock breakdowns.</div>
              )}
            </div>
          );
        })}
      </div>

      {/* Vision / manual holdings import */}
      <div
        style={{
          background: "rgba(255,255,255,0.03)",
          border: "1px solid rgba(124,58,237,0.2)",
          borderRadius: 14,
          padding: "16px 18px",
          marginBottom: 22,
        }}
      >
        <button
          type="button"
          onClick={() => setImportExpanded((v) => !v)}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            background: "transparent",
            border: "none",
            color: "#c4b5fd",
            fontSize: 14,
            fontWeight: 700,
            cursor: "pointer",
            padding: 0,
          }}
        >
          <ImageUp size={18} /> Bulk upload from broker screenshot
        </button>
        {importExpanded && (
          <div style={{ marginTop: 14 }}>
            <div style={{ fontSize: 12, color: "#94a3b8", lineHeight: 1.6, marginBottom: 12 }}>
              Upload one or more broker screenshots (Robinhood, Webull, Fidelity, etc.) — up to 10 images at once.
              Gemini 3.5 Flash extracts holdings from each; results are merged for review before apply.
            </div>
            <label
              style={{
                display: "flex",
                gap: 10,
                alignItems: "flex-start",
                fontSize: 12,
                color: "#94a3b8",
                marginBottom: 12,
                cursor: "pointer",
              }}
            >
              <input
                type="checkbox"
                checked={fullSnapshot}
                onChange={(e) => setFullSnapshot(e.target.checked)}
                style={{ marginTop: 2 }}
              />
              <span>
                This list is my <strong>full</strong> portfolio. TradeTalk will flag tickers
                you hold now but that do not appear in the import as removals when you
                review changes (they are closed on sync). Leave unchecked if the screenshot
                is partial — we only update tickers you include.
              </span>
            </label>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginBottom: 14 }}>
              <label
                style={{
                  ...btnStyle("#374151"),
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 8,
                  cursor: importBusy ? "wait" : "pointer",
                  opacity: importBusy ? 0.6 : 1,
                }}
              >
                <input
                  type="file"
                  accept="image/*"
                  multiple
                  disabled={importBusy}
                  style={{ display: "none" }}
                  onChange={(e) => {
                    runParseImage(e.target.files);
                    e.target.value = "";
                  }}
                />
                {importBusy && importStatus
                  ? importStatus
                  : "Upload screenshot(s)"}
              </label>
            </div>
            {importErr && !reviewOpen ? (
              <div
                style={{
                  fontSize: 12,
                  color: importErr.startsWith("Parsed") ? "#fbbf24" : "#ef4444",
                  marginBottom: 10,
                  lineHeight: 1.5,
                }}
              >
                {importErr}
              </div>
            ) : null}
            <div
              style={{
                fontSize: 11,
                color: "#64748b",
                marginBottom: 8,
                fontWeight: 600,
                letterSpacing: 0.5,
              }}
            >
              OR PASTE MULTIPLE ROWS MANUALLY
            </div>
            {manualDraft.map((row, idx) => (
              <div
                key={idx}
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr 1fr auto",
                  gap: 8,
                  marginBottom: 8,
                }}
              >
                <input
                  placeholder="Ticker"
                  value={row.ticker}
                  onChange={(e) => {
                    const v = [...manualDraft];
                    v[idx] = { ...v[idx], ticker: e.target.value.toUpperCase() };
                    setManualDraft(v);
                  }}
                  style={inputStyle}
                />
                <input
                  placeholder="Shares"
                  value={row.shares}
                  onChange={(e) => {
                    const v = [...manualDraft];
                    v[idx] = { ...v[idx], shares: e.target.value };
                    setManualDraft(v);
                  }}
                  style={inputStyle}
                />
                <input
                  placeholder="Avg cost ($)"
                  value={row.avg_cost}
                  onChange={(e) => {
                    const v = [...manualDraft];
                    v[idx] = { ...v[idx], avg_cost: e.target.value };
                    setManualDraft(v);
                  }}
                  style={inputStyle}
                />
                <button
                  type="button"
                  onClick={() =>
                    setManualDraft((d) => d.filter((_, i) => i !== idx))
                  }
                  style={{ ...btnStyle("#374151"), padding: "9px 12px" }}
                >
                  ×
                </button>
              </div>
            ))}
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
              <button
                type="button"
                onClick={() =>
                  setManualDraft((d) => [
                    ...d,
                    { ticker: "", shares: "", avg_cost: "" },
                  ])
                }
                style={btnStyle("#374151")}
              >
                + Row
              </button>
              <button
                type="button"
                disabled={importBusy}
                onClick={runPreviewManual}
                style={btnStyle("#7c3aed")}
              >
                {importBusy ? "Working…" : "Preview changes"}
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Add position button */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 16,
        }}
      >
        <h3
          style={{ margin: 0, fontSize: 15, fontWeight: 700, color: "#e2e8f0" }}
        >
          Open Positions ({positions.length})
        </h3>
        <button
          onClick={() => setShowAdd((s) => !s)}
          style={{
            padding: "8px 16px",
            borderRadius: 8,
            border: "1px solid rgba(124,58,237,0.4)",
            background: "rgba(124,58,237,0.15)",
            color: "#a78bfa",
            fontSize: 13,
            fontWeight: 600,
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          <Plus size={14} /> Add Stock
        </button>
      </div>

      {/* Add form */}
      {showAdd && (
        <div
          style={{
            background: "rgba(255,255,255,0.04)",
            border: "1px solid rgba(124,58,237,0.2)",
            borderRadius: 14,
            padding: "18px 20px",
            marginBottom: 20,
          }}
        >
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr 1fr 1fr",
              gap: 12,
              marginBottom: 12,
            }}
          >
            <input
              value={addForm.ticker}
              onChange={(e) =>
                setAddForm((f) => ({
                  ...f,
                  ticker: e.target.value.toUpperCase(),
                }))
              }
              placeholder="Ticker (e.g. AAPL)"
              style={inputStyle}
            />
            <select
              value={addForm.direction}
              onChange={(e) =>
                setAddForm((f) => ({ ...f, direction: e.target.value }))
              }
              style={inputStyle}
            >
              <option value="LONG">LONG (Bull)</option>
              <option value="SHORT">SHORT (Bear)</option>
            </select>
            <input
              type="number"
              min="0"
              step="0.01"
              value={addForm.price}
              onChange={(e) =>
                setAddForm((f) => ({ ...f, price: e.target.value }))
              }
              placeholder="Buy price ($)"
              style={inputStyle}
            />
            <input
              type="number"
              min="0"
              step="0.000001"
              value={addForm.shares}
              onChange={(e) =>
                setAddForm((f) => ({ ...f, shares: e.target.value }))
              }
              placeholder="Shares"
              style={inputStyle}
            />
          </div>
          <input
            value={addForm.note}
            onChange={(e) =>
              setAddForm((f) => ({ ...f, note: e.target.value }))
            }
            placeholder="Note (optional — e.g. from AI Debate)"
            style={{
              ...inputStyle,
              width: "100%",
              marginBottom: 10,
              boxSizing: "border-box",
            }}
          />
          {addError && (
            <div style={{ fontSize: 12, color: "#ef4444", marginBottom: 8 }}>
              {addError}
            </div>
          )}
          <div style={{ display: "flex", gap: 10 }}>
            <button
              onClick={handleAdd}
              disabled={adding}
              style={btnStyle("#7c3aed")}
            >
              {adding ? "Adding..." : "Add Stock"}
            </button>
            <button
              onClick={() => setShowAdd(false)}
              style={btnStyle("#374151")}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Positions table */}
      {positions.length === 0 ? (
        <div
          style={{
            padding: "40px 20px",
            textAlign: "center",
            background: "rgba(255,255,255,0.02)",
            border: "1px dashed rgba(255,255,255,0.1)",
            borderRadius: 14,
          }}
        >
          <Target size={32} color="#64748b" style={{ marginBottom: 12 }} />
          <div style={{ fontSize: 15, color: "#64748b" }}>
            No positions yet. Add a stock manually or upload a broker screenshot.
          </div>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {positions.map((pos) => {
            const isPos = pos.pnl_dollar >= 0;
            return (
              <div
                key={pos.id}
                style={{
                  background: "rgba(255,255,255,0.03)",
                  border: "1px solid rgba(255,255,255,0.08)",
                  borderRadius: 12,
                  padding: "14px 18px",
                  display: "grid",
                  gridTemplateColumns: "auto 1fr auto auto auto",
                  gap: "12px 16px",
                  alignItems: "center",
                }}
              >
                {/* Direction badge */}
                <div
                  style={{
                    padding: "4px 8px",
                    borderRadius: 6,
                    fontSize: 10,
                    fontWeight: 700,
                    letterSpacing: 0.5,
                    background:
                      pos.direction === "LONG"
                        ? "rgba(16,185,129,0.15)"
                        : "rgba(239,68,68,0.15)",
                    color: pos.direction === "LONG" ? "#10b981" : "#ef4444",
                  }}
                >
                  {pos.direction}
                </div>

                <div>
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 8,
                      flexWrap: "wrap",
                    }}
                  >
                    <span
                      style={{
                        fontWeight: 700,
                        color: "#e2e8f0",
                        fontSize: 15,
                      }}
                    >
                      {pos.ticker}
                    </span>
                    <span
                      style={{
                        fontSize: 10,
                        fontWeight: 700,
                        letterSpacing: 0.5,
                        textTransform: "uppercase",
                        padding: "2px 8px",
                        borderRadius: 6,
                        background: "rgba(124,58,237,0.2)",
                        color: "#c4b5fd",
                        border: "1px solid rgba(124,58,237,0.35)",
                      }}
                      title="Where this position was recorded from"
                    >
                      {(pos.source || "manual").replace(/_/g, " ")}
                    </span>
                    {[pos.sector, pos.cap_bucket, pos.asset_type].filter(Boolean).map((tag) => (
                      <span
                        key={`${pos.id}-${tag}`}
                        style={{
                          fontSize: 10,
                          fontWeight: 700,
                          letterSpacing: 0.4,
                          padding: "2px 8px",
                          borderRadius: 999,
                          background: "rgba(15,23,42,0.7)",
                          color: "#93c5fd",
                          border: "1px solid rgba(147,197,253,0.25)",
                        }}
                      >
                        {tag}
                      </span>
                    ))}
                  </div>
                  <div style={{ fontSize: 11, color: "#64748b", marginTop: 4 }}>
                    {pos.shares.toFixed(4)} shares · $
                    {pos.entry_price.toFixed(2)} entry
                  </div>
                  {pos.note ? (
                    <div
                      style={{
                        fontSize: 12,
                        color: "#94a3b8",
                        marginTop: 6,
                        fontStyle: "italic",
                      }}
                    >
                      Note: {pos.note}
                    </div>
                  ) : null}
                </div>

                <div style={{ textAlign: "right" }}>
                  <div
                    style={{
                      fontSize: 14,
                      fontWeight: 700,
                      color: isPos ? "#10b981" : "#ef4444",
                    }}
                  >
                    {fmtUSD(pos.pnl_dollar)}
                  </div>
                  <div
                    style={{
                      fontSize: 11,
                      color: isPos ? "#10b981" : "#ef4444",
                    }}
                  >
                    {fmt(pos.pnl_pct)}%
                  </div>
                </div>

                <div style={{ textAlign: "right" }}>
                  <div style={{ fontSize: 12, color: "#94a3b8" }}>
                    ${pos.current_price?.toFixed(2)}
                  </div>
                  <div style={{ fontSize: 11, color: "#64748b" }}>now</div>
                </div>

                <button
                  onClick={() => handleClose(pos.id)}
                  disabled={closing === pos.id}
                  style={{
                    padding: "6px 10px",
                    borderRadius: 6,
                    border: "1px solid rgba(239,68,68,0.3)",
                    background: "rgba(239,68,68,0.08)",
                    color: "#ef4444",
                    fontSize: 11,
                    fontWeight: 600,
                    cursor: "pointer",
                  }}
                >
                  {closing === pos.id ? "..." : "Close"}
                </button>
              </div>
            );
          })}
        </div>
      )}

      {reviewOpen && (
        <div
          role="dialog"
          aria-modal="true"
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.65)",
            zIndex: 10000,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: 16,
          }}
        >
          <div
            style={{
              background: "#0f111a",
              border: "1px solid rgba(124,58,237,0.35)",
              borderRadius: 16,
              maxWidth: 720,
              width: "100%",
              maxHeight: "90vh",
              overflow: "auto",
              padding: "20px 22px",
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: 12,
              }}
            >
              <h3 style={{ margin: 0, color: "#f1f5f9", fontSize: 17 }}>
                Review changes
              </h3>
              <button
                type="button"
                onClick={() => setReviewOpen(false)}
                style={{
                  background: "transparent",
                  border: "none",
                  color: "#94a3b8",
                  cursor: "pointer",
                }}
                aria-label="Close"
              >
                <X size={22} />
              </button>
            </div>
            {reconciliation && (
              <div
                style={{
                  fontSize: 12,
                  color: "#94a3b8",
                  marginBottom: 12,
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))",
                  gap: 8,
                }}
              >
                <span>New: {reconciliation.new?.length ?? 0}</span>
                <span>Updated: {reconciliation.updated?.length ?? 0}</span>
                <span>Unchanged: {reconciliation.unchanged?.length ?? 0}</span>
                {fullSnapshot ? (
                  <span style={{ color: "#f59e0b" }}>
                    Removals: {reconciliation.removed?.length ?? 0}
                  </span>
                ) : null}
              </div>
            )}
            {fullSnapshot &&
            reconciliation?.removed?.length ? (
              <div
                style={{
                  fontSize: 12,
                  color: "#fbbf24",
                  background: "rgba(245,158,11,0.1)",
                  border: "1px solid rgba(245,158,11,0.3)",
                  borderRadius: 8,
                  padding: "10px 12px",
                  marginBottom: 12,
                }}
              >
                These open positions are absent from the import and will be{" "}
                <strong>closed</strong> on sync:{" "}
                {reconciliation.removed.map((r) => r.ticker).join(", ")}
              </div>
            ) : null}
            <div style={{ marginBottom: 10, fontSize: 12, color: "#64748b" }}>
              Edit values before applying. Rows need ticker and shares; leave avg
              cost blank to use the latest market price.
            </div>
            {reviewRows.map((row, idx) => (
              <div
                key={idx}
                style={{
                  display: "grid",
                  gridTemplateColumns: "90px 1fr 1fr 1fr 32px",
                  gap: 8,
                  marginBottom: 8,
                  alignItems: "center",
                }}
              >
                <span style={{ fontSize: 11, color: "#a78bfa" }}>
                  {rowStatus(row.ticker, reconciliation)}
                </span>
                <input
                  placeholder="Ticker"
                  value={row.ticker}
                  onChange={(e) => {
                    const v = [...reviewRows];
                    v[idx] = { ...v[idx], ticker: e.target.value.toUpperCase() };
                    setReviewRows(v);
                  }}
                  style={inputStyle}
                />
                <input
                  placeholder="Shares"
                  value={row.shares}
                  onChange={(e) => {
                    const v = [...reviewRows];
                    v[idx] = { ...v[idx], shares: e.target.value };
                    setReviewRows(v);
                  }}
                  style={inputStyle}
                />
                <input
                  placeholder="Avg cost"
                  value={row.avg_cost}
                  onChange={(e) => {
                    const v = [...reviewRows];
                    v[idx] = { ...v[idx], avg_cost: e.target.value };
                    setReviewRows(v);
                  }}
                  style={inputStyle}
                />
                <button
                  type="button"
                  onClick={() =>
                    setReviewRows((r) => r.filter((_, i) => i !== idx))
                  }
                  style={{
                    background: "transparent",
                    border: "none",
                    color: "#64748b",
                    cursor: "pointer",
                  }}
                >
                  ×
                </button>
              </div>
            ))}
            <button
              type="button"
              onClick={() =>
                setReviewRows((r) => [
                  ...r,
                  { ticker: "", shares: "", avg_cost: "" },
                ])
              }
              style={{ ...btnStyle("#374151"), marginBottom: 12 }}
            >
              + Row
            </button>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
              <button
                type="button"
                disabled={importBusy}
                onClick={applyReview}
                style={btnStyle("#7c3aed")}
              >
                {importBusy ? "Applying…" : "Apply to paper portfolio"}
              </button>
              <button
                type="button"
                onClick={() => setReviewOpen(false)}
                style={btnStyle("#374151")}
              >
                Cancel
              </button>
            </div>
            {importErr && reviewOpen ? (
              <div
                style={{
                  fontSize: 12,
                  color: importErr.startsWith("Parsed") ? "#fbbf24" : "#ef4444",
                  marginTop: 10,
                  lineHeight: 1.5,
                }}
              >
                {importErr}
              </div>
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}

const inputStyle = {
  padding: "10px 12px",
  borderRadius: 8,
  border: "1px solid rgba(255,255,255,0.1)",
  background: "rgba(255,255,255,0.05)",
  color: "#e2e8f0",
  fontSize: 13,
  outline: "none",
  width: "100%",
  boxSizing: "border-box",
};

const btnStyle = (bg) => ({
  padding: "9px 18px",
  borderRadius: 8,
  border: "none",
  background: bg,
  color: "#fff",
  fontSize: 13,
  fontWeight: 600,
  cursor: "pointer",
});

const optionCardStyle = (active) => ({
  display: "flex",
  flexDirection: "column",
  alignItems: "flex-start",
  gap: 8,
  textAlign: "left",
  borderRadius: 16,
  border: active ? "1px solid rgba(167,139,250,0.75)" : "1px solid rgba(255,255,255,0.1)",
  background: active ? "rgba(124,58,237,0.18)" : "rgba(255,255,255,0.04)",
  color: "#e2e8f0",
  padding: "18px 20px",
  cursor: "pointer",
  boxShadow: active ? "0 12px 35px rgba(124,58,237,0.18)" : "none",
});
