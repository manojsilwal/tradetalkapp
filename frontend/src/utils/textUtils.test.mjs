import assert from 'node:assert';
import { enrichText } from './textUtils.js';

console.log('Running enrichText tests...');

// 1. Basic enrichment
{
    const input = 'We should look at short interest for this ticker.';
    const expected = 'We should look at **short interest** for this ticker.';
    const result = enrichText(input);
    assert.strictEqual(result, expected, 'Basic enrichment failed');
    console.log('✓ Basic enrichment passed');
}

// 2. Case-insensitive matching
{
    const input = 'SHORT SQUEEZE is coming.';
    const expected = '**SHORT SQUEEZE** is coming.';
    const result = enrichText(input);
    assert.strictEqual(result, expected, 'Case-insensitive matching failed');
    console.log('✓ Case-insensitive matching passed');
}

// 3. Multiple occurrences of the same term
{
    const input = 'short interest is high. very high short interest.';
    const expected = '**short interest** is high. very high **short interest**.';
    const result = enrichText(input);
    // Note: If this fails, it's because the current regex/replace logic might only replace the first occurrence
    // or if the 'break' statement (which we suspect is a bug for DIFFERENT terms) affects this.
    // Actually, .replace(regex, ...) with 'g' flag should replace all occurrences of THAT term.
    assert.strictEqual(result, expected, 'Multiple occurrences of the same term failed');
    console.log('✓ Multiple occurrences of the same term passed');
}

// 4. Multiple DIFFERENT terms (This is where we suspect a bug)
{
    const input = 'Check the short interest and the vix.';
    const result = enrichText(input);
    const containsShortInterest = result.includes('**short interest**');
    const containsVix = result.includes('**vix**');

    console.log(`Enriched text: "${result}"`);
    if (containsShortInterest && containsVix) {
        console.log('✓ Multiple different terms passed');
    } else {
        console.error('✗ Multiple different terms failed (Suspected BUG confirmed: only one term enriched)');
    }
}

// 5. Edge cases: empty string
{
    assert.strictEqual(enrichText(''), '', 'Empty string failed');
    console.log('✓ Empty string passed');
}

// 6. Edge cases: null/undefined
{
    assert.strictEqual(enrichText(null), null, 'Null failed');
    assert.strictEqual(enrichText(undefined), undefined, 'Undefined failed');
    console.log('✓ Null/undefined passed');
}

// 7. Edge cases: non-string
{
    assert.strictEqual(enrichText(123), 123, 'Non-string failed');
    console.log('✓ Non-string passed');
}

console.log('All tests finished.');
