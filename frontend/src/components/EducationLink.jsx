import { useNavigate } from 'react-router-dom';
import { CONCEPT_MAP, enrichText } from '../utils/textUtils';

export { enrichText };

export function EducationTooltip({ term }) {
    const navigate = useNavigate();
    const key = Object.keys(CONCEPT_MAP).find(k => term.toLowerCase().includes(k));
    if (!key) return null;
    const { path, label } = CONCEPT_MAP[key];

    return (
        <button
            onClick={() => navigate(path)}
            title={label}
            style={{
                display: 'inline-flex', alignItems: 'center', gap: 4,
                padding: '2px 8px', marginLeft: 4, borderRadius: 6,
                fontSize: 11, fontWeight: 600, cursor: 'pointer',
                border: '1px solid rgba(139,92,246,0.3)',
                background: 'rgba(139,92,246,0.08)',
                color: '#a78bfa',
            }}
        >
            📚 {label}
        </button>
    );
}
