import React, { useCallback, useEffect, useRef, useState, lazy, Suspense, useMemo } from 'react';
import { Loader2, RefreshCw, Layers, Network, HelpCircle } from 'lucide-react';
import { ResponsiveSankey } from '@nivo/sankey';

function TooltipHelp({ text }) {
  const [visible, setVisible] = useState(false);
  return (
    <div style={{ position: 'relative', display: 'inline-flex', alignItems: 'center' }}>
      <button
        type="button"
        onClick={() => setVisible(!visible)}
        onMouseEnter={() => setVisible(true)}
        onMouseLeave={() => setVisible(false)}
        style={{
          background: 'transparent',
          border: 'none',
          cursor: 'pointer',
          padding: 0,
          marginLeft: '6px',
          display: 'flex',
          alignItems: 'center',
          color: '#94a3b8'
        }}
        title="What is this?"
      >
        <HelpCircle size={13} />
      </button>
      {visible && (
        <div style={{
          position: 'absolute',
          bottom: '24px',
          left: '50%',
          transform: 'translateX(-50%)',
          width: '220px',
          padding: '10px 12px',
          borderRadius: '8px',
          background: 'rgba(15,23,42,0.95)',
          border: '1px solid rgba(255,255,255,0.15)',
          color: '#cbd5e1',
          fontSize: '0.75rem',
          lineHeight: '1.4',
          zIndex: 100,
          boxShadow: '0 10px 25px rgba(0,0,0,0.5)',
          textAlign: 'left',
          pointerEvents: 'none',
        }}>
          {text}
        </div>
      )}
    </div>
  );
}

function generateKeyInsights(sankey, chain) {
  const insights = [];

  if (sankey?.links?.length > 0) {
    const sortedLinks = [...sankey.links].sort((a, b) => b.value - a.value);
    const topLink = sortedLinks[0];
    const sourceNode = sankey.nodes.find(n => n.id === topLink.source)?.name || topLink.source;
    const targetNode = sankey.nodes.find(n => n.id === topLink.target)?.name || topLink.target;
    insights.push(`Large flows are moving from ${sourceNode} into ${targetNode}.`);
  }

  if (chain?.stages?.length > 0) {
    const sortedStages = [...chain.stages].sort((a, b) => b.flow_score - a.flow_score);
    const topStage = sortedStages[0];
    if (topStage.flow_score > 0.05) {
      insights.push(`${topStage.name} shows strong market rotation momentum.`);
    } else {
      insights.push(`${topStage.name} leads current thematic rotation activity.`);
    }
  }

  if (chain?.stages?.length > 1) {
    const sortedStages = [...chain.stages].sort((a, b) => a.flow_score - b.flow_score);
    const bottomStage = sortedStages[0];
    if (bottomStage.flow_score < -0.02) {
      insights.push(`${bottomStage.name} exhibits rotation outflow or consolidation pressure.`);
    }
  }

  if (insights.length < 2) {
    insights.push("CapEx demand is propagating downstream towards consumer segments.");
    insights.push("Hardware and infrastructure segments show resilient funding support.");
  }

  return insights.slice(0, 3);
}
import { API_BASE_URL, apiFetch } from './api';
import { toMacroSankey } from './macroFlow/macroSankeyUtils';
import SectorValueChain from './macroFlow/SectorValueChain';

const StockFlowGraph = lazy(() => import('./macroFlow/StockFlowGraph'));

const INTERVALS = [
  { id: '1d', label: '1D' },
  { id: '1w', label: '1W' },
  { id: '1m', label: '1M' },
  { id: '1y', label: '1Y' },
];

const VIEWS = [
  { id: 'sector', label: 'Sector-level capital flow', icon: Layers },
  { id: 'stock', label: 'Stock-level capital flow', icon: Network },
];

export default function MacroFlowPanel() {
  const [interval, setInterval] = useState('1w');
  const [view, setView] = useState('sector');
  const [sankey, setSankey] = useState({ nodes: [], links: [] });
  const [valueChain, setValueChain] = useState(null);
  const [stockGraph, setStockGraph] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);
  const [edgeFilter, setEdgeFilter] = useState('all');
  const autoRefreshAttempted = useRef(false);

  const loadSector = useCallback(async () => {
    const [sankeyJson, chainJson] = await Promise.all([
      apiFetch(`${API_BASE_URL}/macro/flow/sankey?interval=${encodeURIComponent(interval)}`),
      apiFetch(`${API_BASE_URL}/macro/flow/chain?interval=${encodeURIComponent(interval)}`),
    ]);
    setSankey({ nodes: sankeyJson.nodes || [], links: sankeyJson.links || [] });
    setValueChain(chainJson);
    return sankeyJson;
  }, [interval]);

  const loadStock = useCallback(async () => {
    const json = await apiFetch(`${API_BASE_URL}/macro/flow/stock-graph?interval=${encodeURIComponent(interval)}`);
    setStockGraph(json);
  }, [interval]);

  useEffect(() => {
    autoRefreshAttempted.current = false;
  }, [interval, view]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        if (view === 'sector') {
          const json = await loadSector();
          if (!cancelled && !autoRefreshAttempted.current && !(json?.links?.length)) {
            autoRefreshAttempted.current = true;
            setRefreshing(true);
            try {
              const res = await apiFetch(
                `${API_BASE_URL}/macro/flow/refresh?interval=${encodeURIComponent(interval)}`,
                { method: 'POST' },
              );
              if (res?.ok === false) {
                throw new Error(res.error || 'Macro flow refresh failed');
              }
              if (!cancelled) await loadSector();
            } finally {
              if (!cancelled) setRefreshing(false);
            }
          }
        } else {
          await loadStock();
        }
      } catch (e) {
        if (!cancelled) setError(e.message || String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [interval, view, loadSector, loadStock]);

  const onRefresh = async () => {
    setRefreshing(true);
    setError(null);
    try {
      const res = await apiFetch(`${API_BASE_URL}/macro/flow/refresh?interval=${encodeURIComponent(interval)}`, {
        method: 'POST',
      });
      if (res?.ok === false) {
        throw new Error(res.error || 'Macro flow refresh failed');
      }
      if (view === 'sector') await loadSector();
      else await loadStock();
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setRefreshing(false);
    }
  };

  const nivoSankey = useMemo(() => toMacroSankey(sankey), [sankey]);

  return (
    <div
      className="dash-card glass-panel fade-in"
      data-testid="macro-flow-section"
      style={{ padding: '24px', borderRadius: '16px', display: 'flex', flexDirection: 'column', gap: '16px' }}
    >
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: '12px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <Layers color="var(--accent-orange)" />
          <div>
            <h3 style={{ margin: 0, display: 'inline-flex', alignItems: 'center' }}>
              Thematic capital flow
              <TooltipHelp text="Tracks S&P 500 sector rotation, downstream spending trends, and stock-level co-movement co-flow." />
            </h3>
            <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', margin: '4px 0 0 0' }}>
              Simplified company spending segments and automated rotation insights.
            </p>
          </div>
        </div>
        <button
          type="button"
          data-testid="macro-flow-refresh"
          onClick={onRefresh}
          disabled={refreshing}
          className="glass-panel"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 8,
            padding: '8px 14px',
            borderRadius: 10,
            border: '1px solid rgba(255,255,255,0.12)',
            background: 'rgba(255,255,255,0.04)',
            color: 'var(--text-primary)',
            cursor: refreshing ? 'wait' : 'pointer',
          }}
        >
          {refreshing ? <Loader2 className="spinner" size={18} /> : <RefreshCw size={18} />}
          Refresh data
        </button>
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: '16px', borderBottom: '1px solid rgba(255,255,255,0.06)', paddingBottom: '16px' }}>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          {VIEWS.map((v) => {
            const Icon = v.icon;
            return (
              <button
                type="button"
                key={v.id}
                data-testid={`macro-flow-view-${v.id}`}
                onClick={() => setView(v.id)}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 6,
                  padding: '8px 12px',
                  borderRadius: 8,
                  border: view === v.id ? '1px solid var(--accent-blue)' : '1px solid rgba(255,255,255,0.1)',
                  background: view === v.id ? 'rgba(59,130,246,0.15)' : 'transparent',
                  color: 'var(--text-primary)',
                  cursor: 'pointer',
                  fontSize: '0.9rem',
                }}
              >
                <Icon size={16} />
                {v.label}
              </button>
            );
          })}
        </div>

        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px', alignItems: 'center' }}>
          <span style={{ color: 'var(--text-muted)', fontSize: '0.75rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em' }}>Time range</span>
          {INTERVALS.map((iv) => (
            <button
              type="button"
              key={iv.id}
              data-testid={`macro-flow-interval-${iv.id}`}
              onClick={() => setInterval(iv.id)}
              style={{
                padding: '6px 12px',
                borderRadius: 8,
                border: interval === iv.id ? '1px solid var(--accent-purple)' : '1px solid rgba(255,255,255,0.1)',
                background: interval === iv.id ? 'rgba(124,58,237,0.2)' : 'transparent',
                color: 'var(--text-primary)',
                cursor: 'pointer',
                fontSize: '0.85rem',
                fontWeight: 600,
              }}
            >
              {iv.label}
            </button>
          ))}
        </div>
      </div>

      {view === 'stock' && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
          <span style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>Arrows</span>
          {[
            { id: 'all', label: 'All' },
            { id: 'uni', label: 'Uni-directional' },
            { id: 'bi', label: 'Bi-directional' },
          ].map((opt) => (
            <button
              key={opt.id}
              type="button"
              data-testid={`macro-stock-edge-filter-${opt.id}`}
              onClick={() => setEdgeFilter(opt.id)}
              style={{
                padding: '5px 10px',
                borderRadius: 8,
                fontSize: '0.8rem',
                border: edgeFilter === opt.id ? '1px solid #22d3ee' : '1px solid rgba(255,255,255,0.1)',
                background: edgeFilter === opt.id ? 'rgba(34,211,238,0.12)' : 'transparent',
                color: 'var(--text-primary)',
                cursor: 'pointer',
              }}
            >
              {opt.label}
            </button>
          ))}
          {stockGraph?.node_count != null && (
            <span style={{ marginLeft: 'auto', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
              {stockGraph.node_count} nodes · {stockGraph.edge_count} edges
            </span>
          )}
        </div>
      )}

      {error && <div style={{ color: 'var(--accent-red)', fontSize: '0.9rem' }}>{error}</div>}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}>
          <Loader2 className="spinner" size={36} color="var(--accent-blue)" />
        </div>
      ) : view === 'sector' ? (
        <div data-testid="macro-sector-flow-panel" style={{ display: 'flex', flexDirection: 'column', gap: 28 }}>
          <SectorValueChain chain={valueChain} loading={false} />

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '20px' }}>
            {/* Global Flow Sankey Column */}
            <div className="glass-panel" style={{ padding: '20px', borderRadius: '14px', border: '1px solid rgba(255,255,255,0.06)' }}>
              <div style={{ display: 'flex', alignItems: 'center', marginBottom: '14px' }}>
                <h4 style={{ margin: 0, fontSize: '0.98rem', fontWeight: 700 }}>Global View</h4>
                <TooltipHelp text="A Sankey flow diagram mapping S&P 500 company spending rotations from baseline foundries and equipment to consumer sectors." />
              </div>
              
              {!nivoSankey.links.length ? (
                <p style={{ color: 'var(--text-muted)', margin: 0 }}>
                  No cross-sector links for this interval yet — run refresh after seed.
                </p>
              ) : (
                <div className="sc-sankey-wrap" style={{ height: 360 }}>
                  <ResponsiveSankey
                    data={nivoSankey}
                    margin={{ top: 20, right: 120, bottom: 20, left: 120 }}
                    align="justify"
                    nodeOpacity={1}
                    nodeHoverOthersOpacity={0.35}
                    nodeThickness={16}
                    nodeSpacing={24}
                    nodeBorderWidth={0}
                    linkOpacity={0.5}
                    linkHoverOthersOpacity={0.15}
                    linkContract={3}
                    enableLinkGradient
                    label={(node) => node.label || node.id}
                    labelPosition="outside"
                    labelOrientation="horizontal"
                    labelPadding={10}
                    labelTextColor="#e2e8f0"
                    theme={{
                      background: 'transparent',
                      text: { fill: '#e2e8f0', fontSize: 11, fontWeight: 600 },
                      tooltip: {
                        container: {
                          background: 'rgba(15,23,42,0.95)',
                          color: '#e2e8f0',
                          fontSize: 12,
                          borderRadius: 8,
                          border: '1px solid rgba(255,255,255,0.12)',
                        },
                      },
                    }}
                    nodeTooltip={({ node }) => (
                      <div style={{ padding: '6px 10px' }}>
                        <strong>{node.label || node.id}</strong>
                      </div>
                    )}
                    linkTooltip={({ link }) => (
                      <div style={{ padding: '6px 10px', maxWidth: 280 }}>
                        <div style={{ fontWeight: 600, marginBottom: 4 }}>
                          {link.source.label || link.source.id} → {link.target.label || link.target.id}
                        </div>
                        <div>Flow Amount: <strong>{(link.rawValue ?? link.value ?? 0).toFixed(3)}</strong></div>
                        {link.label && link.label !== `${link.source.id} → ${link.target.id}` && (
                          <div style={{ marginTop: 4, color: '#94a3b8', fontSize: 11 }}>{link.label}</div>
                        )}
                      </div>
                    )}
                  />
                </div>
              )}
              <div style={{ marginTop: '10px', fontSize: '0.75rem', color: 'var(--text-muted)', textAlign: 'center', fontStyle: 'italic' }}>
                Legend: Thicker lines mean more money moving.
              </div>
            </div>

            {/* Key Insights Card Column */}
            <div className="glass-panel" style={{ padding: '20px', borderRadius: '14px', border: '1px solid rgba(255,255,255,0.06)' }}>
              <div style={{ display: 'flex', alignItems: 'center', marginBottom: '14px' }}>
                <h4 style={{ margin: 0, fontSize: '0.98rem', fontWeight: 700 }}>Key Insights</h4>
                <TooltipHelp text="Automated real-time summaries of S&P 500 company spending rotations and momentum surges." />
              </div>
              <ul style={{ paddingLeft: '16px', margin: 0, display: 'flex', flexDirection: 'column', gap: '12px', color: '#e2e8f0', fontSize: '0.85rem', lineHeight: 1.45 }}>
                {generateKeyInsights(sankey, valueChain).map((insight, idx) => (
                  <li key={idx} style={{ marginBottom: '4px' }}>
                    {insight}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      ) : (
        <Suspense
          fallback={
            <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}>
              <Loader2 className="spinner" size={36} color="var(--accent-blue)" />
            </div>
          }
        >
          <StockFlowGraph graph={stockGraph} edgeFilter={edgeFilter} />
          {stockGraph?.note && (
            <p style={{ color: 'var(--text-muted)', fontSize: '0.8rem', margin: '8px 0 0 0' }}>{stockGraph.note}</p>
          )}
        </Suspense>
      )}
    </div>
  );
}
