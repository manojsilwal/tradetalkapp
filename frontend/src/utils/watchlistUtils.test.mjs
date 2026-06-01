import assert from 'node:assert';
import {
  AI_INFRA_BASKET,
  chunkTickers,
  mapScorecardVerdict,
  mergeWatchlistTickers,
  WATCHLIST_MAX,
} from './watchlistUtils.js';

console.log('Running watchlistUtils tests...');

{
  const chunks = chunkTickers(AI_INFRA_BASKET);
  assert.strictEqual(chunks.length, 2);
  assert.strictEqual(chunks[0].length, 10);
  assert.strictEqual(chunks[1].length, 3);
  console.log('✓ chunkTickers splits 13-symbol basket into 10 + 3');
}

{
  const merged = mergeWatchlistTickers(['AAPL'], AI_INFRA_BASKET);
  assert.strictEqual(merged.length, 14);
  assert.strictEqual(merged[0], 'AAPL');
  assert.ok(merged.includes('GEV'));
  console.log('✓ mergeWatchlistTickers dedupes and preserves order');
}

{
  const many = Array.from({ length: 25 }, (_, i) => `T${i}`);
  const capped = mergeWatchlistTickers([], many);
  assert.strictEqual(capped.length, WATCHLIST_MAX);
  console.log('✓ mergeWatchlistTickers caps at WATCHLIST_MAX');
}

{
  assert.strictEqual(mapScorecardVerdict('Strong'), 'Buy Watch');
  assert.strictEqual(mapScorecardVerdict('Favorable'), 'Buy Watch');
  assert.strictEqual(mapScorecardVerdict('Balanced'), 'Hold / Wait');
  assert.strictEqual(mapScorecardVerdict('Stretched'), 'Overvalued');
  assert.strictEqual(mapScorecardVerdict('Avoid'), 'Avoid');
  console.log('✓ mapScorecardVerdict maps scorecard labels');
}

console.log('All watchlistUtils tests passed.');
