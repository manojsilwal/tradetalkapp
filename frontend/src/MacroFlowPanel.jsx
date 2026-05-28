import React, { useCallback, useEffect, useState, lazy, Suspense, useMemo } from 'react';
import { Loader2, RefreshCw, Layers, Network } from 'lucide-react';
import { ResponsiveSankey } from '@nivo/sankey';
import { API_BASE_URL, apiFetch } from './api';
import { toMacroSankey } from './macroFlow/macroSankeyUtils';

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
  const [stockGraph, setStockGraph] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);
  const [edgeFilter, setEdgeFilter] = useState('all');

  const loadSector = useCallback(async () => {
    const json = await apiFetch(`${API_BASE_URL}/macro/flow/sankey?interval=${encodeURIComponent(interval)}`);
    setSankey({ nodes: json.nodes || [], links: json.links || [] });
  }, [interval]);

  const loadStock = useCallback(async () => {
    const json = await apiFetch(`${API_BASE_URL}/macro/flow/stock-graph?interval=${encodeURIComponent(interval)}`);
    setStockGraph(json);
  }, [interval]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        if (view === 'sector') {
          await loadSector();
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
      await apiFetch(`${API_BASE_URL}/macro/flow/refresh?interval=${encodeURIComponent(interval)}`, {
        method: 'POST',
      });
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
            <h3 style={{ margin: 0 }}>Thematic capital flow</h3>
            <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', margin: '4px 0 0 0' }}>
              Sector rotation and stock-to-stock co-flow across the S&P 500 universe.
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
        <span style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>Time range</span>
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
            }}
          >
            {iv.label}
          </button>
        ))}
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
        <div data-testid="macro-sector-flow-panel">
          {!nivoSankey.links.length ? (
            <p style={{ color: 'var(--text-muted)', margin: 0 }}>
              No sector flows for this interval yet — run refresh after seed.
            </p>
          ) : (
            <div className="sc-sankey-wrap" style={{ height: 400 }}>
              <ResponsiveSankey
                data={nivoSankey}
                margin={{ top: 24, right: 24, bottom: 24, left: 24 }}
                align="justify"
                nodeOpacity={1}
                nodeHoverOthersOpacity={0.35}
                nodeThickness={16}
                nodeSpacing={24}
                nodeBorderWidth={0}
                linkOpacity={0.45}
                linkHoverOthersOpacity={0.15}
                linkContract={3}
                enableLinkGradient
                labelPosition="outside"
                labelOrientation="horizontal"
                labelPadding={8}
                labelTextColor={{ from: 'color', modifiers: [['brighter', 1.4]] }}
                theme={{
                  background: 'transparent',
                  text: { fill: '#cbd5e1', fontSize: 11 },
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
                  <div style={{ padding: '6px 8px' }}>
                    <strong>{node.id}</strong>
                  </div>
                )}
                linkTooltip={({ link }) => (
                  <div style={{ padding: '6px 8px' }}>
                    {link.source.id} → {link.target.id}: <strong>{(link.value || 0).toFixed(3)}</strong>
                  </div>
                )}
              />
            </div>
          )}
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
