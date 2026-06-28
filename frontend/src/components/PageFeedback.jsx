import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Star, X } from 'lucide-react';
import { API_BASE_URL, apiFetch } from '../api';

const COOLDOWN_MS = 7 * 24 * 60 * 60 * 1000;
const REVEAL_IDLE_MS = 2000;

function storageKey(page) {
  return `pf_done:${page}`;
}

function isOnCooldown(page) {
  try {
    const raw = localStorage.getItem(storageKey(page));
    if (!raw) return false;
    const ts = Number(raw);
    if (!Number.isFinite(ts)) return false;
    return Date.now() - ts < COOLDOWN_MS;
  } catch {
    return false;
  }
}

function markCooldown(page) {
  try {
    localStorage.setItem(storageKey(page), String(Date.now()));
  } catch {
    /* ignore */
  }
}

function labelForPage(page) {
  if (page === '/dashboard' || page === '/decision-terminal') {
    return 'Rate this analysis:';
  }
  return 'Rate this page:';
}

export default function PageFeedback({ page, symbol }) {
  const [visible, setVisible] = useState(false);
  const [hoverRating, setHoverRating] = useState(0);
  const [selectedRating, setSelectedRating] = useState(0);
  const [showComment, setShowComment] = useState(false);
  const [comment, setComment] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState('');

  const promptLabel = useMemo(() => labelForPage(page), [page]);

  useEffect(() => {
    setVisible(false);
    setHoverRating(0);
    setSelectedRating(0);
    setShowComment(false);
    setComment('');
    setSubmitting(false);
    setSubmitted(false);
    setError('');

    if (!page || isOnCooldown(page)) {
      return undefined;
    }

    let cancelled = false;
    const reveal = () => {
      if (!cancelled) setVisible(true);
    };

    if (typeof window !== 'undefined' && typeof window.requestIdleCallback === 'function') {
      const idleId = window.requestIdleCallback(reveal, { timeout: REVEAL_IDLE_MS });
      return () => {
        cancelled = true;
        window.cancelIdleCallback(idleId);
      };
    }

    const timer = window.setTimeout(reveal, REVEAL_IDLE_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [page]);

  const dismiss = useCallback(() => {
    markCooldown(page);
    setVisible(false);
  }, [page]);

  const submit = useCallback(async (ratingOverride) => {
    const rating = ratingOverride ?? selectedRating;
    const trimmed = comment.trim();
    if (!rating && !trimmed) {
      setError('Select a star rating or add a comment.');
      return;
    }
    setSubmitting(true);
    setError('');
    try {
      await apiFetch(`${API_BASE_URL}/page-feedback`, {
        method: 'POST',
        body: JSON.stringify({
          page,
          rating: rating || null,
          comment: trimmed || null,
          symbol: symbol || null,
        }),
      });
      markCooldown(page);
      setSubmitted(true);
      setVisible(false);
    } catch (e) {
      setError(e?.message || 'Could not save feedback.');
    } finally {
      setSubmitting(false);
    }
  }, [page, symbol, selectedRating, comment]);

  const onStarClick = (value) => {
    setSelectedRating(value);
    setError('');
    if (!showComment) {
      submit(value);
    }
  };

  if (!visible && !submitted) return null;

  if (submitted) {
    return (
      <div className="page-feedback-bar page-feedback-thanks" data-testid="page-feedback-thanks">
        Thanks for the feedback — it helps us improve TradeTalk.
      </div>
    );
  }

  if (!visible) return null;

  const displayRating = hoverRating || selectedRating;

  return (
    <div className="page-feedback-bar" data-testid="page-feedback">
      <div className="page-feedback-inner">
        <span className="page-feedback-label">{promptLabel}</span>
        <div
          className="page-feedback-stars"
          role="radiogroup"
          aria-label="Star rating"
          onMouseLeave={() => setHoverRating(0)}
        >
          {[1, 2, 3, 4, 5].map((n) => (
            <button
              key={n}
              type="button"
              className={`page-feedback-star${displayRating >= n ? ' is-active' : ''}`}
              aria-label={`${n} star${n > 1 ? 's' : ''}`}
              aria-pressed={selectedRating === n}
              disabled={submitting}
              onMouseEnter={() => setHoverRating(n)}
              onClick={() => onStarClick(n)}
            >
              <Star size={18} fill={displayRating >= n ? 'currentColor' : 'none'} />
            </button>
          ))}
        </div>
        <span className="page-feedback-divider" aria-hidden="true" />
        <button
          type="button"
          className="page-feedback-comment-toggle"
          onClick={() => setShowComment((v) => !v)}
          disabled={submitting}
        >
          Add detailed comment
        </button>
        <button
          type="button"
          className="page-feedback-dismiss"
          aria-label="Dismiss feedback"
          onClick={dismiss}
          disabled={submitting}
        >
          <X size={16} />
        </button>
      </div>
      {showComment && (
        <div className="page-feedback-comment-panel">
          <textarea
            className="page-feedback-textarea"
            rows={3}
            maxLength={2000}
            placeholder="What worked well? What should we improve?"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            disabled={submitting}
          />
          <div className="page-feedback-comment-actions">
            <button
              type="button"
              className="page-feedback-submit"
              onClick={() => submit()}
              disabled={submitting}
            >
              {submitting ? 'Sending…' : 'Submit feedback'}
            </button>
            {error && <span className="page-feedback-error">{error}</span>}
          </div>
        </div>
      )}
      {!showComment && error && (
        <div className="page-feedback-error-inline">{error}</div>
      )}
    </div>
  );
}
