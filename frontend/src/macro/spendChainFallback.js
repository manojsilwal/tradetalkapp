/**
 * Offline fallback for spend-chain when /macro/spend-chain is unavailable
 * (e.g. older backend deploy). Mirrors backend/data/supply_chains.json est. 2026.
 */
export const SPEND_CHAIN_FALLBACK = {
  latest_year: '2026',
  source: 'supply_chains.json',
  spend_flow_groups: [
    {
      from_stage_id: 'retail_industry',
      to_stage_id: 'hyperscaler',
      from_stage_name: 'Retail / Industry',
      to_stage_name: 'Hyperscaler',
      description: 'Enterprise & cloud capex demand',
      latest_year: '2026',
      top_spenders: [
        { entity_id: 'OPENAI', entity_name: 'OpenAI', spend_usd: 10100000000 },
        { entity_id: 'LLY', entity_name: 'Eli Lilly', spend_usd: 300000000 },
      ],
      top_beneficiaries: [
        { entity_id: 'MSFT', entity_name: 'Microsoft', spend_usd: 8000000000 },
        { entity_id: 'AMZN', entity_name: 'Amazon (AWS)', spend_usd: 1500000000 },
        { entity_id: 'GOOGL', entity_name: 'Google (GCP)', spend_usd: 600000000 },
      ],
      pairs: [
        { spender_id: 'OPENAI', spender_name: 'OpenAI', beneficiary_id: 'MSFT', beneficiary_name: 'Microsoft', spend_usd: 8000000000, relationship_type: 'compute' },
        { spender_id: 'OPENAI', spender_name: 'OpenAI', beneficiary_id: 'AMZN', beneficiary_name: 'Amazon (AWS)', spend_usd: 1500000000, relationship_type: 'compute' },
        { spender_id: 'OPENAI', spender_name: 'OpenAI', beneficiary_id: 'GOOGL', beneficiary_name: 'Google (GCP)', spend_usd: 600000000, relationship_type: 'compute' },
        { spender_id: 'LLY', spender_name: 'Eli Lilly', beneficiary_id: 'OPENAI', beneficiary_name: 'OpenAI', spend_usd: 300000000, relationship_type: 'subscription' },
      ],
    },
    {
      from_stage_id: 'hyperscaler',
      to_stage_id: 'semiconductor',
      from_stage_name: 'Hyperscaler',
      to_stage_name: 'Semiconductor',
      description: 'GPU / accelerator orders',
      latest_year: '2026',
      top_spenders: [
        { entity_id: 'MSFT', entity_name: 'Microsoft', spend_usd: 34000000000 },
        { entity_id: 'AMZN', entity_name: 'Amazon (AWS)', spend_usd: 31000000000 },
        { entity_id: 'GOOGL', entity_name: 'Google (GCP)', spend_usd: 20000000000 },
      ],
      top_beneficiaries: [
        { entity_id: 'NVDA', entity_name: 'NVIDIA', spend_usd: 78000000000 },
        { entity_id: 'AVGO', entity_name: 'Broadcom', spend_usd: 9000000000 },
      ],
      pairs: [
        { spender_id: 'MSFT', spender_name: 'Microsoft', beneficiary_id: 'NVDA', beneficiary_name: 'NVIDIA', spend_usd: 30000000000, relationship_type: 'capex' },
        { spender_id: 'AMZN', spender_name: 'Amazon (AWS)', beneficiary_id: 'NVDA', beneficiary_name: 'NVIDIA', spend_usd: 28000000000, relationship_type: 'capex' },
        { spender_id: 'GOOGL', spender_name: 'Google (GCP)', beneficiary_id: 'NVDA', beneficiary_name: 'NVIDIA', spend_usd: 20000000000, relationship_type: 'capex' },
        { spender_id: 'MSFT', spender_name: 'Microsoft', beneficiary_id: 'AVGO', beneficiary_name: 'Broadcom', spend_usd: 4000000000, relationship_type: 'capex' },
        { spender_id: 'AMZN', spender_name: 'Amazon (AWS)', beneficiary_id: 'AVGO', beneficiary_name: 'Broadcom', spend_usd: 3000000000, relationship_type: 'capex' },
      ],
    },
    {
      from_stage_id: 'semiconductor',
      to_stage_id: 'foundry_infra',
      from_stage_name: 'Semiconductor',
      to_stage_name: 'Foundry / Equipment',
      description: 'Fab capacity, lithography & packaging',
      latest_year: '2026',
      top_spenders: [
        { entity_id: 'NVDA', entity_name: 'NVIDIA', spend_usd: 38000000000 },
        { entity_id: 'AAPL', entity_name: 'Apple', spend_usd: 20000000000 },
        { entity_id: 'AVGO', entity_name: 'Broadcom', spend_usd: 7000000000 },
      ],
      top_beneficiaries: [
        { entity_id: 'TSM', entity_name: 'TSMC', spend_usd: 65000000000 },
      ],
      pairs: [
        { spender_id: 'NVDA', spender_name: 'NVIDIA', beneficiary_id: 'TSM', beneficiary_name: 'TSMC', spend_usd: 38000000000, relationship_type: 'manufacturing' },
        { spender_id: 'AAPL', spender_name: 'Apple', beneficiary_id: 'TSM', beneficiary_name: 'TSMC', spend_usd: 20000000000, relationship_type: 'manufacturing' },
        { spender_id: 'AVGO', spender_name: 'Broadcom', beneficiary_id: 'TSM', beneficiary_name: 'TSMC', spend_usd: 7000000000, relationship_type: 'manufacturing' },
      ],
    },
    {
      from_stage_id: 'foundry_infra',
      to_stage_id: 'materials',
      from_stage_name: 'Foundry / Equipment',
      to_stage_name: 'Materials / Minerals',
      description: 'Wafers, chemicals & rare-earth inputs',
      latest_year: '2026',
      top_spenders: [
        { entity_id: 'TSM', entity_name: 'TSMC', spend_usd: 26700000000 },
        { entity_id: 'CATL', entity_name: 'CATL', spend_usd: 1900000000 },
      ],
      top_beneficiaries: [
        { entity_id: 'ASML', entity_name: 'ASML', spend_usd: 18000000000 },
        { entity_id: 'LRCX', entity_name: 'Lam Research', spend_usd: 4500000000 },
        { entity_id: 'KLAC', entity_name: 'KLA Corporation', spend_usd: 2200000000 },
        { entity_id: 'ALB', entity_name: 'Albemarle', spend_usd: 1100000000 },
        { entity_id: 'SQM', entity_name: 'SQM', spend_usd: 800000000 },
      ],
      pairs: [
        { spender_id: 'TSM', spender_name: 'TSMC', beneficiary_id: 'ASML', beneficiary_name: 'ASML', spend_usd: 18000000000, relationship_type: 'equipment' },
        { spender_id: 'TSM', spender_name: 'TSMC', beneficiary_id: 'LRCX', beneficiary_name: 'Lam Research', spend_usd: 4500000000, relationship_type: 'equipment' },
        { spender_id: 'TSM', spender_name: 'TSMC', beneficiary_id: 'KLAC', beneficiary_name: 'KLA Corporation', spend_usd: 2200000000, relationship_type: 'equipment' },
        { spender_id: 'CATL', spender_name: 'CATL', beneficiary_id: 'ALB', beneficiary_name: 'Albemarle', spend_usd: 1100000000, relationship_type: 'raw_materials' },
        { spender_id: 'CATL', spender_name: 'CATL', beneficiary_id: 'SQM', beneficiary_name: 'SQM', spend_usd: 800000000, relationship_type: 'raw_materials' },
      ],
    },
  ],
};
