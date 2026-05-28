import React, { memo, useMemo } from 'react';
import { ResponsiveSankey } from '@nivo/sankey';
import { toNivoSankey, fmtUSD } from './utils';
import './supplyChainViz.css';

function SectorSankeyViz({ data, year }) {
  const sankeyData = useMemo(() => toNivoSankey(data), [data]);

  if (!sankeyData.links.length) {
    return <p style={{ color: 'var(--text-muted)', padding: 24 }}>No sector flows for {year}.</p>;
  }

  return (
    <div className="sc-sankey-wrap" data-testid="supply-chain-sankey">
      <ResponsiveSankey
        data={sankeyData}
        margin={{ top: 24, right: 24, bottom: 24, left: 24 }}
        align="justify"
        nodeOpacity={1}
        nodeHoverOthersOpacity={0.35}
        nodeThickness={14}
        nodeSpacing={20}
        nodeBorderWidth={0}
        nodeBorderRadius={3}
        nodeInnerPadding={3}
        nodeColor={(n) => n.nodeColor || '#6366f1'}
        linkOpacity={0.45}
        linkHoverOthersOpacity={0.15}
        linkContract={3}
        linkBlendMode="screen"
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
            {link.source.id} → {link.target.id}: <strong>{fmtUSD(link.value)}</strong>
          </div>
        )}
      />
    </div>
  );
}

export default memo(SectorSankeyViz);
