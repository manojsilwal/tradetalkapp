import React, { useCallback, useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { API_BASE_URL, apiFetch } from '../api';
import { YEARS, CHAINS, VIEW_MODES } from './constants';
import { fmtUSD } from './utils';
import SupplyChainFlowGraph from './SupplyChainFlowGraph';
import SectorSankeyViz from './SectorSankeyViz';
import TimelineMorphView from './TimelineMorphView';
import './supplyChainViz.css';

export default function SupplyChainTab() {
  const [year, setYear] = useState(2025);
  const [root, setRoot] = useState('LLY');
  const [viewMode, setViewMode] = useState('graph');
  const [graph, setGraph] = useState(null);
  const [sectorSankey, setSectorSankey] = useState(null);
  const [graphSnapshots, setGraphSnapshots] = useState([]);
  const [sectorSnapshots, setSectorSnapshots] = useState([]);
  const [selectedNode, setSelectedNode] = useState(null);
  const [nodeDetail, setNodeDetail] = useState(null);
  const [loading, setLoading] = useState(false);
  const [timelineLoading, setTimelineLoading] = useState(false);
  const [error, setError] = useState(null);

  const loadGraph = useCallback(async () => {
    const params = new URLSearchParams();
    params.set('year', String(year));
    if (root) params.set('root', root);
    const data = await apiFetch(`${API_BASE_URL}/macro/supply-chain/graph?${params}`);
    setGraph({ ...data, root });
    return data;
  }, [year, root]);

  const loadSectorSankey = useCallback(async () => {
    const data = await apiFetch(`${API_BASE_URL}/macro/supply-chain/sector-sankey?year=${year}`);
    setSectorSankey(data);
    return data;
  }, [year]);

  const loadTimelines = useCallback(async () => {
    setTimelineLoading(true);
    try {
      const params = new URLSearchParams({ from: '2020', to: '2026' });
      if (root) params.set('root', root);
      const [graphTl, sectorTl] = await Promise.all([
        apiFetch(`${API_BASE_URL}/macro/supply-chain/timeline?${params}`),
        apiFetch(`${API_BASE_URL}/macro/supply-chain/sector-sankey/timeline?from=2020&to=2026`),
      ]);
      setGraphSnapshots(graphTl.snapshots || []);
      setSectorSnapshots(sectorTl.snapshots || []);
    } finally {
      setTimelineLoading(false);
    }
  }, [root]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        if (viewMode === 'graph' || viewMode === 'table') {
          await loadGraph();
        } else if (viewMode === 'sankey') {
          await loadSectorSankey();
        } else if (viewMode === 'timeline') {
          await loadTimelines();
        }
      } catch (e) {
        if (!cancelled) setError(e.message || 'Failed to load supply chain data');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [viewMode, loadGraph, loadSectorSankey, loadTimelines]);

  useEffect(() => {
    if (viewMode === 'timeline') return;
    loadTimelines();
  }, [root, loadTimelines, viewMode]);

  const onNodeClick = async (nodeId) => {
    setSelectedNode(nodeId);
    try {
      const data = await apiFetch(
        `${API_BASE_URL}/macro/supply-chain/nodes/${encodeURIComponent(nodeId)}?year=${year}`
      );
      setNodeDetail(data);
    } catch {
      setNodeDetail(null);
    }
  };

  const selectStyle = {
    padding: '6px 10px',
    borderRadius: 8,
    border: '1px solid rgba(255,255,255,0.15)',
    background: 'rgba(255,255,255,0.05)',
    color: 'var(--text-primary)',
  };

  const showSpinner = loading || (viewMode === 'timeline' && timelineLoading && !graphSnapshots.length);

  return (
    <div data-testid="supply-chain-tab" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center' }}>
        {viewMode !== 'timeline' && (
          <>
            <span style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>Year</span>
            <select value={year} onChange={(e) => setYear(Number(e.target.value))} style={selectStyle}>
              {YEARS.map((y) => (
                <option key={y} value={y}>
                  {y}
                </option>
              ))}
            </select>
          </>
        )}

        <span style={{ color: 'var(--text-muted)', fontSize: '0.85rem', marginLeft: viewMode === 'timeline' ? 0 : 8 }}>
          Chain
        </span>
        <select
          value={root ?? ''}
          onChange={(e) => setRoot(e.target.value || null)}
          style={selectStyle}
        >
          {CHAINS.map((c) => (
            <option key={c.id ?? '__all'} value={c.id ?? ''}>
              {c.label}
            </option>
          ))}
        </select>

        <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {VIEW_MODES.map((m) => (
            <button
              key={m.id}
              type="button"
              data-testid={`supply-chain-view-${m.id}`}
              onClick={() => setViewMode(m.id)}
              style={{
                padding: '6px 12px',
                borderRadius: 8,
                cursor: 'pointer',
                border: viewMode === m.id ? '1px solid #22d3ee' : '1px solid rgba(255,255,255,0.1)',
                background: viewMode === m.id ? 'rgba(34,211,238,0.12)' : 'transparent',
                color: 'var(--text-primary)',
                fontSize: '0.85rem',
              }}
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>

      {error && <div style={{ color: 'var(--accent-red)', fontSize: '0.9rem' }}>{error}</div>}

      {showSpinner ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}>
          <Loader2 className="spinner" size={36} color="var(--accent-blue)" />
        </div>
      ) : viewMode === 'graph' ? (
        <SupplyChainFlowGraph graph={graph} highlightId={selectedNode} onNodeClick={onNodeClick} />
      ) : viewMode === 'sankey' ? (
        <SectorSankeyViz data={sectorSankey} year={year} />
      ) : viewMode === 'timeline' ? (
        <TimelineMorphView
          year={year}
          onYearChange={setYear}
          graphSnapshots={graphSnapshots}
          sectorSnapshots={sectorSnapshots}
          highlightId={selectedNode}
          onNodeClick={onNodeClick}
          root={root}
        />
      ) : (
        <CompanyEdgesTable graph={graph} onNodeClick={onNodeClick} />
      )}

      {selectedNode && nodeDetail && (
        <NodeDetailDrawer
          detail={nodeDetail}
          year={year}
          onClose={() => {
            setSelectedNode(null);
            setNodeDetail(null);
          }}
        />
      )}
    </div>
  );
}

function CompanyEdgesTable({ graph, onNodeClick }) {
  if (!graph?.edges?.length) {
    return <p style={{ color: 'var(--text-muted)' }}>No edges for this year / chain.</p>;
  }
  const nodeMap = Object.fromEntries((graph.nodes || []).map((n) => [n.node_id, n]));
  return (
    <div style={{ overflowX: 'auto' }} data-testid="supply-chain-table">
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
        <thead>
          <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.1)', color: 'var(--text-muted)' }}>
            <th style={{ textAlign: 'left', padding: '8px 6px' }}>Source</th>
            <th style={{ textAlign: 'left', padding: '8px 6px' }}>Target</th>
            <th style={{ textAlign: 'left', padding: '8px 6px' }}>Type</th>
            <th style={{ textAlign: 'right', padding: '8px 6px' }}>Est. annual</th>
            <th style={{ textAlign: 'center', padding: '8px 6px' }}>Confidence</th>
          </tr>
        </thead>
        <tbody>
          {graph.edges.map((e) => (
            <tr key={e.edge_id} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
              <td style={{ padding: '6px' }}>
                <NodeChip node={nodeMap[e.source_node_id]} onClick={() => onNodeClick(e.source_node_id)} />
              </td>
              <td style={{ padding: '6px' }}>
                <span style={{ color: 'var(--text-muted)', marginRight: 4 }}>→</span>
                <NodeChip node={nodeMap[e.target_node_id]} onClick={() => onNodeClick(e.target_node_id)} />
              </td>
              <td style={{ padding: '6px', color: 'var(--text-muted)' }}>{e.relationship_type || '—'}</td>
              <td style={{ padding: '6px', textAlign: 'right', fontFamily: 'monospace' }}>
                {fmtUSD(e.amount_est_usd)}
              </td>
              <td style={{ padding: '6px', textAlign: 'center' }}>
                <ConfidenceDot value={e.confidence} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function NodeChip({ node, onClick }) {
  if (!node) return <span style={{ color: 'var(--text-muted)' }}>?</span>;
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        padding: '3px 8px',
        borderRadius: 6,
        border: '1px solid rgba(255,255,255,0.12)',
        background: node.is_public ? 'rgba(34,197,94,0.1)' : 'rgba(249,115,22,0.1)',
        color: 'var(--text-primary)',
        cursor: 'pointer',
        fontSize: '0.85rem',
      }}
    >
      <span style={{ fontWeight: 700 }}>{node.ticker || node.node_id}</span>
      <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{node.gics_sector}</span>
    </button>
  );
}

function ConfidenceDot({ value }) {
  const v = value ?? 0;
  const color = v >= 0.7 ? '#22c55e' : v >= 0.4 ? '#eab308' : '#ef4444';
  return (
    <span
      title={`${(v * 100).toFixed(0)}%`}
      style={{
        display: 'inline-block',
        width: 10,
        height: 10,
        borderRadius: '50%',
        background: color,
        opacity: 0.9,
      }}
    />
  );
}

function NodeDetailDrawer({ detail, year, onClose }) {
  const { node, upstream, downstream } = detail;
  return (
    <div
      style={{
        background: 'rgba(15,23,42,0.95)',
        backdropFilter: 'blur(12px)',
        border: '1px solid rgba(255,255,255,0.1)',
        borderRadius: 14,
        padding: '18px 20px',
        marginTop: 4,
      }}
      data-testid="supply-chain-node-drawer"
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <div>
          <div style={{ fontWeight: 800, fontSize: '1.1rem' }}>
            {node.name} {node.ticker ? `(${node.ticker})` : ''}
          </div>
          <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
            {node.gics_sector}
            {node.gics_sub_industry ? ` / ${node.gics_sub_industry}` : ''}
            {!node.is_public && <span style={{ marginLeft: 8, color: '#f97316' }}>Private</span>}
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          style={{
            background: 'none',
            border: 'none',
            color: 'var(--text-muted)',
            cursor: 'pointer',
            fontSize: 18,
          }}
        >
          ✕
        </button>
      </div>
      {upstream.length > 0 && (
        <div style={{ marginBottom: 10 }}>
          <div style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--text-muted)', marginBottom: 4 }}>
            Pays this entity ({year})
          </div>
          {upstream.map((e) => (
            <div key={e.edge_id} style={{ fontSize: '0.85rem', marginBottom: 2 }}>
              {e.source_node_id} → <strong>{node.node_id}</strong> — {fmtUSD(e.amount_est_usd)} ({e.relationship_type})
            </div>
          ))}
        </div>
      )}
      {downstream.length > 0 && (
        <div>
          <div style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--text-muted)', marginBottom: 4 }}>
            Receives from this entity ({year})
          </div>
          {downstream.map((e) => (
            <div key={e.edge_id} style={{ fontSize: '0.85rem', marginBottom: 2 }}>
              <strong>{node.node_id}</strong> → {e.target_node_id} — {fmtUSD(e.amount_est_usd)} ({e.relationship_type})
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
