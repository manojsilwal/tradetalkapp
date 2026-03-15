import React, { useState, useEffect } from 'react';
import { BookOpen, CheckCircle2, Lock, ChevronRight, Star, Zap, Award } from 'lucide-react';
import { API_BASE_URL, apiFetch } from './api';

export default function LearningPathUI({ onXpGained }) {
    const [curriculum, setCurriculum] = useState(null);
    const [activeModule, setActiveModule] = useState(null);
    const [moduleDetail, setModuleDetail] = useState(null);
    const [quizState, setQuizState]   = useState(null);   // {answers: {}, submitted: bool, score: int}
    const [loading, setLoading]       = useState(true);
    const [submitting, setSubmitting] = useState(false);

    useEffect(() => {
        apiFetch(`${API_BASE_URL}/learning/curriculum`)
            .then(data => setCurriculum(data))
            .catch(() => {})
            .finally(() => setLoading(false));
    }, []);

    const openModule = async (modId) => {
        setActiveModule(modId);
        setQuizState({ answers: {}, submitted: false, score: 0 });
        const data = await apiFetch(`${API_BASE_URL}/learning/module/${modId}`);
        setModuleDetail(data);
    };

    const handleQuizAnswer = (qIdx, aIdx) => {
        if (quizState?.submitted) return;
        setQuizState(s => ({ ...s, answers: { ...s.answers, [qIdx]: aIdx } }));
    };

    const submitQuiz = async () => {
        if (!moduleDetail || submitting) return;
        const quiz  = moduleDetail.quiz || [];
        const score = quiz.reduce((acc, q, i) =>
            quizState.answers[i] === q.a ? acc + 1 : acc, 0);
        setSubmitting(true);
        try {
            const data = await apiFetch(`${API_BASE_URL}/learning/module/${activeModule}/complete`, {
                method: 'POST',
                body: JSON.stringify({ score }),
            });
            setQuizState(s => ({ ...s, submitted: true, score, result: data }));
            if (data.progress && onXpGained) onXpGained(data.progress);
            // Refresh curriculum
            const updated = await apiFetch(`${API_BASE_URL}/learning/curriculum`);
            setCurriculum(updated);
        } finally {
            setSubmitting(false);
        }
    };

    if (loading) return (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}>
            <div style={{ width: 40, height: 40, border: '3px solid rgba(255,255,255,0.1)', borderTopColor: '#a78bfa', borderRadius: '50%', animation: 'spin 0.8s linear infinite' }} />
        </div>
    );

    // Module detail view
    if (activeModule && moduleDetail) {
        const quiz   = moduleDetail.quiz || [];
        const isQuiz = quiz.length > 0;
        const submitted = quizState?.submitted;
        const result = quizState?.result;

        return (
            <div style={{ maxWidth: 680, margin: '0 auto', padding: '0 16px' }}>
                <button
                    onClick={() => { setActiveModule(null); setModuleDetail(null); }}
                    style={{ background: 'none', border: 'none', color: '#a78bfa', fontSize: 13, cursor: 'pointer', marginBottom: 20, display: 'flex', alignItems: 'center', gap: 6 }}
                >
                    ← Back to curriculum
                </button>

                {/* Module header */}
                <div style={{
                    background: 'linear-gradient(135deg, rgba(124,58,237,0.15), rgba(59,130,246,0.1))',
                    border: '1px solid rgba(124,58,237,0.2)',
                    borderRadius: 16, padding: '20px 24px', marginBottom: 24,
                }}>
                    <div style={{ fontSize: 11, color: '#a78bfa', fontWeight: 700, letterSpacing: 1.5, marginBottom: 4 }}>
                        LEVEL {moduleDetail.level} · {moduleDetail.level_title}
                    </div>
                    <h2 style={{ margin: '0 0 8px', fontSize: 22, color: '#fff' }}>{moduleDetail.title}</h2>
                    <p style={{ margin: 0, fontSize: 13, color: '#94a3b8' }}>{moduleDetail.description}</p>
                    {moduleDetail.app_feature && (
                        <div style={{ marginTop: 10, fontSize: 12, color: '#f59e0b' }}>
                            📍 Practice in: {featureName(moduleDetail.app_feature)}
                        </div>
                    )}
                </div>

                {/* Guided steps */}
                {moduleDetail.guided_steps?.length > 0 && (
                    <div style={{ marginBottom: 24 }}>
                        <h3 style={{ fontSize: 13, fontWeight: 700, color: '#64748b', letterSpacing: 1, marginBottom: 14 }}>
                            GUIDED STEPS
                        </h3>
                        {moduleDetail.guided_steps.map((step, i) => (
                            <div key={i} style={{
                                display: 'flex', gap: 14, marginBottom: 12,
                                padding: '12px 16px',
                                background: 'rgba(255,255,255,0.03)',
                                border: '1px solid rgba(255,255,255,0.06)',
                                borderRadius: 10,
                            }}>
                                <div style={{
                                    width: 24, height: 24, borderRadius: '50%', flexShrink: 0,
                                    background: 'rgba(124,58,237,0.3)',
                                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                                    fontSize: 11, fontWeight: 700, color: '#a78bfa',
                                }}>
                                    {i + 1}
                                </div>
                                <span style={{ fontSize: 13, color: '#cbd5e1', lineHeight: 1.6 }}>{step}</span>
                            </div>
                        ))}
                    </div>
                )}

                {/* Quiz */}
                {isQuiz && !moduleDetail.is_assessment && (
                    <div>
                        <h3 style={{ fontSize: 13, fontWeight: 700, color: '#64748b', letterSpacing: 1, marginBottom: 14 }}>
                            KNOWLEDGE CHECK ({quiz.length} questions)
                        </h3>
                        {quiz.map((q, qi) => (
                            <QuizQuestion key={qi} q={q} qi={qi} state={quizState} onAnswer={handleQuizAnswer} />
                        ))}

                        {!submitted ? (
                            <button
                                onClick={submitQuiz}
                                disabled={Object.keys(quizState?.answers || {}).length < quiz.length || submitting}
                                style={{
                                    width: '100%', padding: '14px', borderRadius: 12, border: 'none',
                                    background: Object.keys(quizState?.answers || {}).length >= quiz.length
                                        ? 'linear-gradient(135deg, #7c3aed, #a78bfa)' : 'rgba(255,255,255,0.05)',
                                    color: Object.keys(quizState?.answers || {}).length >= quiz.length ? '#fff' : '#64748b',
                                    fontSize: 15, fontWeight: 700, cursor: 'pointer',
                                }}
                            >
                                {submitting ? 'Submitting...' : 'Submit Quiz'}
                            </button>
                        ) : (
                            <QuizResult result={result} score={quizState.score} total={quiz.length} />
                        )}
                    </div>
                )}

                {/* Assessment */}
                {isQuiz && moduleDetail.is_assessment && (
                    <div>
                        <div style={{
                            padding: '14px 18px', borderRadius: 12, marginBottom: 20,
                            background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.2)',
                            fontSize: 13, color: '#f59e0b',
                        }}>
                            ⚠️ This is a Level Assessment. You need {moduleDetail.pass_score}/{quiz.length} to pass and unlock the next level.
                        </div>
                        {quiz.map((q, qi) => (
                            <QuizQuestion key={qi} q={q} qi={qi} state={quizState} onAnswer={handleQuizAnswer} />
                        ))}
                        {!submitted ? (
                            <button
                                onClick={submitQuiz}
                                disabled={Object.keys(quizState?.answers || {}).length < quiz.length || submitting}
                                style={{
                                    width: '100%', padding: '14px', borderRadius: 12, border: 'none',
                                    background: 'linear-gradient(135deg, #d97706, #f59e0b)',
                                    color: '#fff', fontSize: 15, fontWeight: 700, cursor: 'pointer',
                                }}
                            >
                                {submitting ? 'Submitting...' : 'Submit Assessment'}
                            </button>
                        ) : (
                            <QuizResult result={result} score={quizState.score} total={quiz.length} passScore={moduleDetail.pass_score} />
                        )}
                    </div>
                )}
            </div>
        );
    }

    // Curriculum overview
    return (
        <div style={{ maxWidth: 800, margin: '0 auto', padding: '0 16px' }}>
            <div style={{ marginBottom: 24 }}>
                <h2 style={{ margin: '0 0 4px', fontSize: 22, color: '#fff' }}>Investor Learning Path</h2>
                <p style={{ margin: 0, fontSize: 13, color: '#64748b' }}>
                    {curriculum?.total_modules} modules · structured curriculum following real-world investment analysis
                </p>
            </div>

            {curriculum?.levels?.map(level => (
                <LevelSection key={level.level} level={level} onOpenModule={openModule} />
            ))}
        </div>
    );
}

function LevelSection({ level, onOpenModule }) {
    const allDone = level.modules.every(m => m.completed);
    return (
        <div style={{ marginBottom: 28 }}>
            <div style={{
                display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14,
                padding: '12px 18px',
                background: allDone ? 'rgba(16,185,129,0.08)' : 'rgba(255,255,255,0.03)',
                border: `1px solid ${allDone ? 'rgba(16,185,129,0.2)' : 'rgba(255,255,255,0.07)'}`,
                borderRadius: 12,
            }}>
                <div style={{
                    width: 32, height: 32, borderRadius: '50%',
                    background: allDone ? 'rgba(16,185,129,0.2)' : 'rgba(124,58,237,0.2)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontSize: 13, fontWeight: 800,
                    color: allDone ? '#10b981' : '#a78bfa',
                }}>
                    {level.level}
                </div>
                <div>
                    <div style={{ fontSize: 15, fontWeight: 700, color: '#e2e8f0' }}>{level.title}</div>
                    <div style={{ fontSize: 11, color: '#64748b' }}>
                        {level.modules.filter(m => m.completed).length}/{level.modules.length} modules complete
                    </div>
                </div>
                {allDone && <CheckCircle2 size={20} color="#10b981" style={{ marginLeft: 'auto' }} />}
            </div>

            {level.modules.map(mod => (
                <ModuleCard key={mod.id} mod={mod} onOpen={() => !mod.locked && onOpenModule(mod.id)} />
            ))}
        </div>
    );
}

function ModuleCard({ mod, onOpen }) {
    return (
        <div
            onClick={onOpen}
            style={{
                marginBottom: 8,
                padding: '14px 18px',
                borderRadius: 12,
                background: mod.completed ? 'rgba(16,185,129,0.06)' : mod.locked ? 'rgba(255,255,255,0.02)' : 'rgba(255,255,255,0.04)',
                border: `1px solid ${mod.completed ? 'rgba(16,185,129,0.2)' : mod.locked ? 'rgba(255,255,255,0.05)' : 'rgba(255,255,255,0.08)'}`,
                cursor: mod.locked ? 'not-allowed' : 'pointer',
                display: 'flex', alignItems: 'center', gap: 14,
                opacity: mod.locked ? 0.5 : 1,
                transition: 'transform 0.15s, background 0.2s',
            }}
            onMouseEnter={e => { if (!mod.locked) e.currentTarget.style.transform = 'translateX(4px)'; }}
            onMouseLeave={e => { e.currentTarget.style.transform = 'none'; }}
        >
            <div style={{
                width: 36, height: 36, borderRadius: 10, flexShrink: 0,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                background: mod.completed ? 'rgba(16,185,129,0.2)' : mod.locked ? 'rgba(100,116,139,0.2)' : 'rgba(124,58,237,0.2)',
            }}>
                {mod.locked ? <Lock size={16} color="#64748b" /> :
                 mod.completed ? <CheckCircle2 size={16} color="#10b981" /> :
                 <BookOpen size={16} color="#a78bfa" />}
            </div>

            <div style={{ flex: 1 }}>
                <div style={{
                    fontSize: 14, fontWeight: 600,
                    color: mod.locked ? '#64748b' : mod.completed ? '#10b981' : '#e2e8f0',
                }}>
                    {mod.title}
                    {mod.is_assessment && <span style={{ fontSize: 10, color: '#f59e0b', marginLeft: 6, fontWeight: 700 }}>ASSESSMENT</span>}
                </div>
                <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>
                    {mod.description}
                </div>
            </div>

            <div style={{ textAlign: 'right', flexShrink: 0 }}>
                {mod.completed ? (
                    <div style={{ fontSize: 11, color: '#10b981', fontWeight: 600 }}>
                        {mod.score}/{mod.quiz_count}
                    </div>
                ) : (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 4, color: '#f59e0b', fontSize: 12 }}>
                        <Zap size={12} /> {mod.xp}xp
                    </div>
                )}
                <ChevronRight size={14} color="#64748b" />
            </div>
        </div>
    );
}

function QuizQuestion({ q, qi, state, onAnswer }) {
    const selected  = state?.answers?.[qi];
    const submitted = state?.submitted;
    return (
        <div style={{ marginBottom: 20 }}>
            <p style={{ fontSize: 14, fontWeight: 600, color: '#e2e8f0', marginBottom: 10, lineHeight: 1.5 }}>
                Q{qi + 1}. {q.q}
            </p>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {q.opts.map((opt, oi) => {
                    const isSel  = selected === oi;
                    const isRight = submitted && oi === q.a;
                    const isWrong = submitted && isSel && oi !== q.a;
                    return (
                        <button key={oi} onClick={() => onAnswer(qi, oi)} style={{
                            padding: '10px 14px', borderRadius: 8, border: `1px solid ${
                                isRight ? 'rgba(16,185,129,0.6)'
                              : isWrong ? 'rgba(239,68,68,0.6)'
                              : isSel   ? 'rgba(124,58,237,0.5)'
                              : 'rgba(255,255,255,0.07)'}`,
                            background: isRight ? 'rgba(16,185,129,0.12)' : isWrong ? 'rgba(239,68,68,0.12)' : isSel ? 'rgba(124,58,237,0.12)' : 'rgba(255,255,255,0.03)',
                            color: isRight ? '#10b981' : isWrong ? '#ef4444' : isSel ? '#a78bfa' : '#94a3b8',
                            fontSize: 13, textAlign: 'left', cursor: submitted ? 'default' : 'pointer',
                            fontWeight: isSel || isRight ? 600 : 400,
                        }}>
                            {String.fromCharCode(65 + oi)}. {opt}
                        </button>
                    );
                })}
            </div>
        </div>
    );
}

function QuizResult({ result, score, total, passScore }) {
    const passed = result?.passed;
    return (
        <div style={{
            marginTop: 20, padding: '20px 24px', borderRadius: 14,
            background: passed ? 'rgba(16,185,129,0.1)' : 'rgba(239,68,68,0.1)',
            border: `1px solid ${passed ? 'rgba(16,185,129,0.3)' : 'rgba(239,68,68,0.3)'}`,
        }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
                {passed ? <CheckCircle2 size={24} color="#10b981" /> : <Star size={24} color="#ef4444" />}
                <span style={{ fontSize: 18, fontWeight: 800, color: passed ? '#10b981' : '#ef4444' }}>
                    {passed ? `Passed! +${result?.xp_awarded} XP` : `${score}/${total} — Try again`}
                </span>
            </div>
            {passScore && <div style={{ fontSize: 13, color: '#94a3b8' }}>
                Pass mark: {passScore}/{total}
            </div>}
        </div>
    );
}

function featureName(id) {
    const map = { consumer: 'Valuation Dashboard', macro: 'Global Macro', debate: 'AI Debate', backtest: 'Strategy Lab', observer: 'Developer Trace' };
    return map[id] || id;
}
