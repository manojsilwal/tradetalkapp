import React, { useState, useEffect, useCallback } from "react";
import { useLocation } from "react-router-dom";
import { Plus, Target, ImageUp, X } from "lucide-react";
import { API_BASE_URL, apiFetch, apiPostMultipart } from "./api";

const fmt = (n, dec = 2) => (n >= 0 ? "+" : "") + n.toFixed(dec);
const fmtUSD = (n) => (n >= 0 ? "+$" : "-$") + Math.abs(n).toFixed(2);

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
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [showAdd, setShowAdd] = useState(false);
  const [addForm, setAddForm] = useState({
    ticker: "",
    direction: "LONG",
    allocated: 1000,
    note: "",
  });
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState("");
  const [closing, setClosing] = useState(null);

  const [importExpanded, setImportExpanded] = useState(false);
  const [fullSnapshot, setFullSnapshot] = useState(false);
  const [importBusy, setImportBusy] = useState(false);
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
    const file = fileList?.[0];
    if (!file) return;
    setImportBusy(true);
    setImportErr("");
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("full_snapshot", fullSnapshot ? "true" : "false");
      const data = await apiPostMultipart(
        `${API_BASE_URL}/portfolio/parse-holdings-image`,
        fd
      );
      openReview(data);
    } catch (e) {
      setImportErr(e.message || "Could not parse screenshot");
    } finally {
      setImportBusy(false);
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
    try {
      setLoading(true);
      const data = await apiFetch(`${API_BASE_URL}/portfolio/performance`);
      setPerf(data);
      if (data.beating_spy && onXpGained) {
        // Silently poll — don't spam awards
      }
    } catch (e) {
      setError("Failed to load portfolio");
    } finally {
      setLoading(false);
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
    setAdding(true);
    setAddError("");
    try {
      const data = await apiFetch(`${API_BASE_URL}/portfolio/position`, {
        method: "POST",
        body: JSON.stringify(addForm),
      });
      if (data.error) {
        setAddError(data.error);
        return;
      }
      if (onXpGained) onXpGained({ xp_awarded: 10, new_badges: [] });
      setShowAdd(false);
      setAddForm({ ticker: "", direction: "LONG", allocated: 1000, note: "" });
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

  if (loading)
    return (
      <div style={{ display: "flex", justifyContent: "center", padding: 60 }}>
        <div
          className="spinner"
          style={{
            width: 40,
            height: 40,
            border: "3px solid rgba(255,255,255,0.1)",
            borderTopColor: "#a78bfa",
            borderRadius: "50%",
            animation: "spin 0.8s linear infinite",
          }}
        />
      </div>
    );

  const positions = perf?.positions || [];
  const beatingSPY = perf?.beating_spy;

  return (
    <div style={{ maxWidth: 800, margin: "0 auto", padding: "0 16px" }}>
      {/* Portfolio Summary */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, 1fr)",
          gap: 14,
          marginBottom: 24,
        }}
      >
        {[
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
            value: `${fmt(perf?.total_pnl_pct || 0)}% vs ${fmt(perf?.spy_pnl_pct || 0)}%`,
            sub: beatingSPY ? "🔥 Beating the market!" : "SPY is ahead",
            color: beatingSPY ? "#10b981" : "#f59e0b",
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
          <ImageUp size={18} /> Import holdings (screenshot or manual)
        </button>
        {importExpanded && (
          <div style={{ marginTop: 14 }}>
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
                  disabled={importBusy}
                  style={{ display: "none" }}
                  onChange={(e) => {
                    runParseImage(e.target.files);
                    e.target.value = "";
                  }}
                />
                Upload Robinhood / broker screenshot
              </label>
            </div>
            <div
              style={{
                fontSize: 11,
                color: "#64748b",
                marginBottom: 8,
                fontWeight: 600,
                letterSpacing: 0.5,
              }}
            >
              OR ENTER MANUALLY
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
            {importErr ? (
              <div style={{ fontSize: 12, color: "#ef4444", marginTop: 10 }}>
                {importErr}
              </div>
            ) : null}
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
          <Plus size={14} /> Add Position
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
              gridTemplateColumns: "1fr 1fr 1fr",
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
              value={addForm.allocated}
              onChange={(e) =>
                setAddForm((f) => ({ ...f, allocated: Number(e.target.value) }))
              }
              placeholder="Amount ($)"
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
              {adding ? "Adding..." : "Add Position"}
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
            No positions yet. Add a position after running a debate or
            valuation.
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
              <div style={{ fontSize: 12, color: "#ef4444", marginTop: 10 }}>
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
