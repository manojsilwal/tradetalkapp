import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { HelpCircle } from 'lucide-react';
import { useAuth } from '../AuthContext';
import {
  momentumAcademyPath,
  persistMomentumAcademyContext,
} from '../utils/momentumAcademyContext';

export function extractMomentumReadout(valuation) {
  const model = (valuation?.models || []).find((m) => m.name === 'Momentum');
  return model?.momentum_summary ?? null;
}

function fmtScore(v) {
  if (v == null || Number.isNaN(Number(v))) return '—';
  return Number(v).toFixed(1);
}

function truncate(text, max = 100) {
  if (!text) return '';
  const s = String(text).trim();
  if (s.length <= max) return s;
  return `${s.slice(0, max - 1)}…`;
}

/** Short hover summary — headline scores only. */
export function momentumBriefText(readout, provenance) {
  if (!readout) {
    return provenance?.missing_reason || 'Momentum model not available.';
  }
  const parts = [
    `Momentum ${fmtScore(readout.momentum_pricing_score)}/100`,
    `Downside ${fmtScore(readout.downside_exposure_score)}/100`,
    `Quality ${fmtScore(readout.decision_quality_score)}/100`,
  ];
  if (readout.classification) parts.push(readout.classification);
  if (readout.crash_risk) parts.push(`Crash risk: ${readout.crash_risk}`);
  return parts.join(' · ');
}

/**
 * Momentum row label + ? control.
 * Hover: brief tooltip. Click (admin only): Investor Academy lesson with ticker context.
 */
export function MomentumInfoTip({
  readout,
  provenance,
  label = 'Momentum:',
  ticker,
  valuation,
}) {
  const navigate = useNavigate();
  const { user } = useAuth();
  const isAdmin = Boolean(user?.is_admin);
  const [hover, setHover] = useState(false);
  const sym = (ticker || readout?.ticker || '').trim().toUpperCase();

  const handleClick = (e) => {
    if (!isAdmin) return;
    e.preventDefault();
    persistMomentumAcademyContext({ ticker: sym, readout, valuation });
    navigate(momentumAcademyPath(sym));
  };

  return (
    <span
      className="dt-momentum-tip-wrap"
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      <span>{label}</span>
      <button
        type="button"
        className="dt-tip dt-momentum-tip-btn"
        onClick={handleClick}
        aria-label={
          isAdmin
            ? (sym
              ? `Open full momentum lesson for ${sym} in Investor Academy`
              : 'Open full momentum lesson in Investor Academy')
            : 'Momentum model scores'
        }
        data-testid="momentum-model-tip-trigger"
        style={isAdmin ? undefined : { cursor: 'help' }}
      >
        <HelpCircle size={11} className="dt-tip-icon" />
      </button>
      {hover && (
        <div
          className="dt-momentum-hover-tip"
          role="tooltip"
          data-testid="momentum-model-hover-tip"
        >
          <p className="dt-momentum-hover-tip-main">{momentumBriefText(readout, provenance)}</p>
          {readout?.model_read && (
            <p className="dt-momentum-hover-tip-read">{truncate(readout.model_read, 110)}</p>
          )}
          {isAdmin ? (
            <p className="dt-momentum-hover-tip-hint">
              {sym
                ? `Click for the full ${sym} walkthrough in Investor Academy`
                : 'Click for the full guided lesson in Investor Academy'}
            </p>
          ) : (
            <p className="dt-momentum-hover-tip-hint">Hover for momentum scores.</p>
          )}
        </div>
      )}
    </span>
  );
}
